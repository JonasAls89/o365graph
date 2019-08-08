from flask import Flask, request, Response, abort
import os
import sys
import requests
import logging
import json
from dotdictify import dotdictify
from time import sleep
from urllib.parse import urlparse


app = Flask(__name__)

# Environment variables
required_env_vars = ["client_id", "client_secret", "grant_type", "resource", "entities_path", "next_page", "token_url"]
optional_env_vars = ["log_level", "base_url", "sleep", "port", "sharepoint_url"]


class AppConfig(object):
    pass


config = AppConfig()

# load variables
missing_env_vars = list()
for env_var in required_env_vars:
    value = os.getenv(env_var)
    if not value:
        missing_env_vars.append(env_var)
    setattr(config, env_var, value)

for env_var in optional_env_vars:
    value = os.getenv(env_var)
    if value:
        setattr(config, env_var, value)

# Set up logging
format_string = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
logger = logging.getLogger('o365graph-service')
stdout_handler = logging.StreamHandler()
stdout_handler.setFormatter(logging.Formatter(format_string))
logger.addHandler(stdout_handler)

loglevel = getattr(config, "log_level", "INFO")
level = logging.getLevelName(loglevel.upper())
if not isinstance(level, int):
    logger.warning("Unsupported log level defined. Using default level 'INFO'")
    level = logging.INFO
logger.setLevel(level)


if len(missing_env_vars) != 0:
    logger.error(f"Missing the following required environment variable(s) {missing_env_vars}")
    sys.exit(1)


def set_group_id(entity):
    for k, v in entity.items():
        if k.split(":")[-1] == "id":
            groupid = v
            logger.info(groupid)
        else:
            pass
    return groupid


class Graph:

    def __init__(self):
        self.session = None
        self.auth_header = None
        self.graph_url = getattr(config, "base_url", None) or "https://graph.microsoft.com/v1.0/"

    def get_token(self):
        payload = {
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "grant_type": config.grant_type,
            "resource": config.resource
        }
        logger.info("Acquiring new access token")
        resp = requests.post(url=config.token_url, data=payload)
        if not resp.ok:
            logger.error(f"Access token request failed. Error: {resp.content}")
            raise
        access_token = resp.json().get("access_token")
        self.auth_header = {"Authorization": "Bearer " + access_token}

    def request(self, method, url, **kwargs):
        if not self.session:
            self.session = requests.Session()
            self.get_token()

        req = requests.Request(method, url, headers=self.auth_header, **kwargs)

        resp = self.session.send(req.prepare())
        if resp.status_code == 401:
            self.get_token()
            resp = self.session.send(req.prepare())

        return resp

    def __get_all_paged_entities(self, path, args):
        logger.info(f"Fetching data from paged url: {path}")
        url = self.graph_url + path
        next_page = url
        page_counter = 1
        while next_page is not None:
            if hasattr(config, "sleep"):
                logger.info(f"sleeping for {config.sleep} milliseconds")
                sleep(float(config.sleep))

            logger.info(f"Fetching data from url: {next_page}")
            if "$skiptoken" not in next_page:
                req = self.request("GET", next_page, params=args)
            else:
                req = self.request("GET", next_page)

            if not req.ok:
                error_text = f"Unexpected response status code: {req.status_code} with response text {req.text}"
                logger.error(error_text)
                raise AssertionError(error_text)
            res = dotdictify(req.json())
            for entity in res.get(config.entities_path):

                yield(entity)

            if res.get(config.next_page) is not None:
                page_counter += 1
                next_page = res.get(config.next_page)
            else:
                next_page = None
        logger.info(f"Returning entities from {page_counter} pages")

    def __get_all_siteurls(self, posted_entities):
        logger.info('fetching site urls')
        for entity in posted_entities:
            url = self.graph_url + "groups/" + set_group_id(entity) + "/sites/root"
            req = self.request("GET", url)
            if not req.ok:
                logger.info('no url')
            else:
                res = dotdictify(req.json())
                res['_id'] = set_group_id(entity)

                yield res

    def get_paged_entities(self, path, args):
        print("getting all paged")
        return self.__get_all_paged_entities(path, args)

    def get_siteurls(self, posted_entities):
        print("getting all siteurls")
        return self.__get_all_siteurls(posted_entities)

    def _get_sharepoint_site_id(self, site):
        """Find the sharepoint id for a given site or team based on site's relative url"""

        url = self.graph_url + "sites/" + site
        logger.debug(f"sharepoint site id url: '{url}'")
        resp = self.request("GET", url)
        if not resp.ok:
            logger.error(f"Unable to determine site id for site '{site}'. Error: {resp.text}")
            return None
        return resp.json().get("id")

    def _get_site_documents_drive_url(self, site):
        """Find the drive id for the sharepoint site/team documents directory"""

        site_id = self._get_sharepoint_site_id(site)
        if site_id:
            url = self.graph_url + "/sites/" + site_id + "/drive"
            logger.debug(f"site documents drive url: '{url}'")
            resp = self.request("GET", url)
            if not resp.ok:
                logger.error(f"Unable to determine documents drive id for site '{site}'. Error: {resp.text}")
                return None
            drive_id = resp.json().get("id")
            drive_url = url + "s/" + drive_id + "/root:/"
            return drive_url
        logger.error("Unable to determine documents drive id without a valid site_id")
        return None

    def _get_file_download_url(self, path, site):
        """Get the file download url for a given file path in given sharepoint site/team"""
        drive_url = self._get_site_documents_drive_url(site)
        if drive_url:
            url = drive_url + path
            logger.debug(f"File details request url: '{url}'")
            resp = self.request("GET", url)
            if not resp.ok:
                logger.error(f"Failed to get download url for file '{path}' on '{site}'. Error: {resp.text}")
                return None
            return resp.json().get("@microsoft.graph.downloadUrl")
        logger.error("Unable to determine download url without valid drive url.")
        return None

    def get_file(self, path, site):
        """Get file from sharepoint file directory"""

        download_url = data_access_layer._get_file_download_url(path, site)
        logger.debug(f"File download url: '{download_url}'")
        resp = requests.get(download_url)  # No auth required for this url
        if not resp.ok:
            logger.error(f"Failed to retrieve file from path '{path}'. Error: {resp.text}")
            return None

        return resp.content



data_access_layer = Graph()


def stream_json(entities):
    first = True
    yield '['
    for i, row in enumerate(entities):
        if not first:
            yield ','
        else:
            first = False
        yield json.dumps(row)
    yield ']'


# def set_updated(entity, args):
#     since_path = args.get("since_path")
#
#     if since_path is not None:
#         b = Dotdictify(entity)
#         entity["_updated"] = b.get(since_path)

# def rename(entity):
#     for key, value in entity.items():
#         res = dict(entity[key.split(':')[1]]=entity.pop(key))
#     logger.info(res)
#     return entity['id']


@app.route("/entities/<path:path>", methods=["GET", "POST"])
def get(path):
    if request.method == "POST":
        path = request.get_json()

    if request.method == "GET":
        path = path

    entities = data_access_layer.get_paged_entities(path, args=request.args)

    return Response(
        stream_json(entities),
        mimetype='application/json'
    )


@app.route("/siteurl", methods=["POST"])
def getsite():
    posted_entities = request.get_json()
    entities = data_access_layer.get_siteurls(posted_entities)

    return Response(
        stream_json(entities),
        mimetype='application/json'
    )


@app.route("/file/<path:path>", methods=["GET"])
def get_file(path):
    # /teams/SesamPOC/data_export/RXindex/steinfoss.csv

    sharepoint_url = getattr(config, "sharepoint_url", None)
    if not sharepoint_url:
        return "Missing environment variable 'sharepoint_url' to use this url path", 500

    sharepoint_url = urlparse(sharepoint_url).netloc

    logger.info(path)
    url_parts = path.split("/")
    if len(url_parts) < 3:
        logger.error(f"Invalid path specified: '{path}'")
        abort(400)

    site = sharepoint_url + ":/" + "/".join(url_parts[:2])
    path = "/".join(url_parts[2:])

    file_resp = data_access_layer.get_file(path, site)
    if file_resp:
        return file_resp

    abort(500)


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', threaded=True, port=getattr(config, 'port', 5000))

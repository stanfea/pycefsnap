#!/usr/bin/python
# -*- coding: latin-1 -*-
# https://github.com/cztomczak/cefpython/blob/master/src/linux/binaries_64bit/wxpython-response.py

import ctypes, os, sys
libcef_so = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'libcef.so')
if os.path.exists(libcef_so):
    # Import local module
    ctypes.CDLL(libcef_so, ctypes.RTLD_GLOBAL)
    if 0x02070000 <= sys.hexversion < 0x03000000:
        import cefpython_py27 as cefpython
    else:
        raise Exception("Unsupported python version: %s" % sys.version)
else:
    # Import from package
    from cefpython3 import cefpython
from PIL import Image
from time import time
import traceback
import json
import multiprocessing.pool
import lxml.html
import logging
from collections import OrderedDict
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)


def save_html(fpath, html):
    with open(fpath, 'w') as f:
        f.write(html)


def save_json(fpath, data):
    with open(fpath, 'w') as f:
        json.dump(data, f)


def save_image(fpath, image, width, height):
    image = Image.frombytes("RGBA", (width, height), image, "raw", "RGBA", 0, 1)
    if os.path.exists(fpath):
        os.remove(fpath)
    image.save(fpath, "PNG")


class ResourceHandler:

    # The methods of this class will always be called
    # on the IO thread.

    _resourceHandlerId = None
    _clientHandler = None
    _command = None
    _browser = None
    _frame = None
    _request = None
    _responseHeadersReadyCallback = None
    _webRequest = None
    _webRequestClient = None
    _offsetRead = 0

    def ProcessRequest(self, request, callback):
        # 1. Start the request using WebRequest
        # 2. Return True to handle the request
        # 3. Once response headers are ready call
        #    callback.Continue()
        self._responseHeadersReadyCallback = callback
        self._webRequestClient = WebRequestClient()
        self._webRequestClient._resourceHandler = self
        # Need to set AllowCacheCredentials and AllowCookies for
        # the cookies to work during POST requests (Issue 127).
        # To skip cache set the SkipCache request flag.
        request.SetFlags(cefpython.Request.Flags["AllowCachedCredentials"] \
                         | cefpython.Request.Flags["AllowCookies"])
        if 'headers' in self._command:
            headers = request.GetHeaderMap()  # keep headers set by cookieManager
            headers.update(self._command['headers'])
            request.SetHeaderMap(headers)


        # A strong reference to the WebRequest object must kept.
        self._webRequest = cefpython.WebRequest.Create(
            request, self._webRequestClient)
        return True

    def GetResponseHeaders(self, response, responseLengthOut, redirectUrlOut):
        # 1. If the response length is not known set
        #    responseLengthOut[0] to -1 and ReadResponse()
        #    will be called until it returns False.
        # 2. If the response length is known set
        #    responseLengthOut[0] to a positive value
        #    and ReadResponse() will be called until it
        #    returns False or the specified number of bytes
        #    have been read.
        # 3. Use the |response| object to set the mime type,
        #    http status code and other optional header values.
        # 4. To redirect the request to a new URL set
        #    redirectUrlOut[0] to the new url.
        assert self._webRequestClient._response, "Response object empty"
        wrcResponse = self._webRequestClient._response
        response.SetStatus(wrcResponse.GetStatus())
        response.SetStatusText(wrcResponse.GetStatusText())
        response.SetMimeType(wrcResponse.GetMimeType())
        if wrcResponse.GetHeaderMultimap():
            response.SetHeaderMultimap(wrcResponse.GetHeaderMultimap())
        responseLengthOut[0] = self._webRequestClient._dataLength
        if not responseLengthOut[0]:
            # Probably a cached page? Or a redirect?
            pass

    def ReadResponse(self, dataOut, bytesToRead, bytesReadOut, callback):
        # print("ReadResponse()")
        # 1. If data is available immediately copy up to
        #    bytesToRead bytes into dataOut[0], set
        #    bytesReadOut[0] to the number of bytes copied,
        #    and return true.
        # 2. To read the data at a later time set
        #    bytesReadOut[0] to 0, return true and call
        #    callback.Continue() when the data is available.
        # 3. To indicate response completion return false.
        if self._offsetRead < self._webRequestClient._dataLength:
            dataChunk = self._webRequestClient._data[ \
                        self._offsetRead:(self._offsetRead + bytesToRead)]
            self._offsetRead += len(dataChunk)
            dataOut[0] = dataChunk
            bytesReadOut[0] = len(dataChunk)
            return True
        self._clientHandler._ReleaseStrongReference(self)
        return False

    def CanGetCookie(self, cookie):
        # Return true if the specified cookie can be sent
        # with the request or false otherwise. If false
        # is returned for any cookie then no cookies will
        # be sent with the request.
        return True

    def CanSetCookie(self, cookie):
        # Return true if the specified cookie returned
        # with the response can be set or false otherwise.
        return True

    def Cancel(self):
        # Request processing has been canceled.
        pass


class WebRequestClient:

    _resourceHandler = None
    _data = ""
    _dataLength = -1
    _response = None

    def OnUploadProgress(self, webRequest, current, total):
        pass

    def OnDownloadProgress(self, webRequest, current, total):
        pass

    def OnDownloadData(self, webRequest, data):
        self._data += data

    def OnRequestComplete(self, webRequest):
        # cefpython.WebRequest.Status = {"Unknown", "Success",
        #         "Pending", "Canceled", "Failed"}
        statusText = "Unknown"
        if webRequest.GetRequestStatus() in cefpython.WebRequest.Status:
            statusText = cefpython.WebRequest.Status[webRequest.GetRequestStatus()]
        #print("status = %s" % statusText)
        #print("error code = %s" % webRequest.GetRequestError())
        # Emulate OnResourceResponse() in ClientHandler:
        self._response = webRequest.GetResponse()
        # Are webRequest.GetRequest() and
        # self._resourceHandler._request the same? What if
        # there was a redirect, what will GetUrl() return
        # for both of them?
        self._data = self._resourceHandler._clientHandler._OnResourceResponse(
            self._resourceHandler._browser,
            self._resourceHandler._frame,
            webRequest.GetRequest(),
            webRequest.GetRequestStatus(),
            webRequest.GetRequestError(),
            webRequest.GetResponse(),
            self._data)
        self._dataLength = len(self._data)
        # ResourceHandler.GetResponseHeaders() will get called
        # after _responseHeadersReadyCallback.Continue() is called.
        self._resourceHandler._responseHeadersReadyCallback.Continue()


class ClientHandler:
    """A client handler is required for the browser to do built in callbacks back into the application."""
    browser = None
    command = None
    doneEnd = False
    isLoading = True

    def __init__(self, browser, command):
        self.browser = browser
        self.command = command

    def OnPaint(self, browser, paintElementType, dirtyRects, buffer, bufferWidth, bufferHeight):
        if paintElementType == cefpython.PET_POPUP:
            return
        elif paintElementType == cefpython.PET_VIEW:
            image = buffer.GetString(mode="rgba", origin="top-left")
            browser.SetUserData("image", image)
        else:
            raise Exception("Unknown paintElementType: %s" % paintElementType)

    def GetViewRect(self, browser, rect):
        width = browser.GetUserData("width")
        height = browser.GetUserData("height")
        rect.append(0)
        rect.append(0)
        rect.append(width)
        rect.append(height)
        return True

    def GetScreenPoint(self, browser, viewX, viewY, screenCoordinates):
        return False

    def OnLoadEnd(self, browser, frame, httpStatusCode):
        if self.isLoading or self.doneEnd:
            return

        self.doneEnd = True
        metadata = browser.GetUserData("metadata")
        if httpStatusCode != 200:
            metadata['error'] = "Failed loading page got status code %s" % httpStatusCode
            cefpython.QuitMessageLoop()
            return
        logging.info("Finished loading page")
        metadata['loaded'] = 1
        metadata['content'] = 1
        metadata['content_time'] = int(time())
        metadata['response_code'] = httpStatusCode
        browser.SetUserData("metadata", metadata)
        frame = browser.GetMainFrame()
        if 'size' in self.command and self.command['size'] != "screen":
            frame.ExecuteJavascript('''
                scrollWidth = document.body.scrollWidth; scrollHeight = document.body.scrollHeight;
                clientWidth = document.body.clientWidth; clientHeight = document.body.clientHeight;
                width = (scrollWidth > clientWidth) ? scrollWidth + 10 : clientWidth;
                height = (scrollHeight > clientHeight) ? scrollHeight + 10 : clientHeight;
                setPageSize(width, height);''')
        if 'script' in self.command:
            logging.info("Executing user JS")
            frame.ExecuteJavascript(self.command['script'])
        frame.ExecuteJavascript('''
            function flashOnPage() {
                objects = document.querySelectorAll("object");
                for (var i=0; i < objects.length; i++) {
                    if ("classid" in objects[i].attributes || objects[i].type == "application/x-shockwave-flash") return true;
                }
             return false;
            };
            if (flashOnPage()) { timeout = flash_delay; log("Detected flash")}
            else { timeout = delay; log("Didn\'t detect flash")}
            log("Waiting " + timeout + "s");
            setTimeout(function() { jsCallback(document.documentElement.innerHTML);}, timeout * 1000);''')

    def OnConsoleMessage(self, browser, message, source, line):
        logging.debug(message)
        return True

    def OnLoadError(self, browser, frame, errorCode, errorText, failedURL):
        metadata = browser.GetUserData("metadata")
        metadata['error'] = errorText
        browser.SetUserData("metadata", metadata)
        cefpython.QuitMessageLoop()

    def GetCookieManager(self, browser, mainUrl):
        cookieManager = browser.GetUserData("cookieManager")
        return cookieManager

    def OnLoadingStateChange(self, browser, isloading, cangoback, cangoforward):
        self.isLoading = isloading


    def GetResourceHandler(self, browser, frame, request):
        # Called on the IO thread before a resource is loaded.
        # To allow the resource to load normally return None.
        resHandler = ResourceHandler()
        resHandler._clientHandler = self
        resHandler._command = self.command
        resHandler._browser = browser
        resHandler._frame = frame
        resHandler._request = request
        self._AddStrongReference(resHandler)
        return resHandler

    def _OnResourceResponse(self, browser, frame, request, requestStatus, requestError, response, data):
        metadata = browser.GetUserData("metadata")
        metadata['content_type'] = response.GetMimeType()
        metadata['status']= response.GetStatusText()
        browser.SetUserData("metadata", metadata)
        return data

    # A strong reference to ResourceHandler must be kept
    # during the request. Some helper functions for that.
    # 1. Add reference in GetResourceHandler()
    # 2. Release reference in ResourceHandler.ReadResponse()
    #    after request is completed.

    _resourceHandlers = {}
    _resourceHandlerMaxId = 0

    def _AddStrongReference(self, resHandler):
        self._resourceHandlerMaxId += 1
        resHandler._resourceHandlerId = self._resourceHandlerMaxId
        self._resourceHandlers[resHandler._resourceHandlerId] = resHandler

    def _ReleaseStrongReference(self, resHandler):
        if resHandler._resourceHandlerId in self._resourceHandlers:
            del self._resourceHandlers[resHandler._resourceHandlerId]
        else:
            print("_ReleaseStrongReference() FAILED: resource handler " \
                  "not found, id = %s" % (resHandler._resourceHandlerId))


def print_usage():
    print "Usage: python " + sys.argv[0] + " <input file>"
    print"""
The input file contains JSON data in this format:
{"post_data":"","delay":"6","file":"/tmp/1400469755-c8f149c0ecbafcde1b083619fb99c186-60-
5885443.png","shot_interval":5,"size":"screen","html":0,"thumbalizr":1,"url":"http://www.google.fr/1",
"id":"5885443","referer":"","screen_height":"1024","instance_id":"60","script":"","shots":1,"cookie":"
","details":2,"flash_delay":0,"screen_width":"1280", “proxies”: [“a.com”, “b.com”], "timeout": 30}

- post_data: option POST DATA to be send with the URL
- delay: time to wait in seconds after the page load event to take a screenshot
- flash_delay: time to wait in seconds after the page load event to take a screenshot if Flash
elements are present in the page
- file: base file name to save the screenshot and metadata
- size: size of the screenshot: screen (visible browser size) or page (full page)
- html: 1 if the rendered HTML needs to be saved, 0 otherwise
- url: URL of the page to load
- referer: optional Referrer header
- screen_height: height of the browser (viewport)
- screen_width: width of the browser(viewport)
- script: option URL of Javascript file to execute inside the browser
- cookie: optional Cookie header
- details: level of details in the metadata
- timeout: timeout in seconds
"""


def setPageSize(width, height):
    browser = cefpython.GetBrowserByWindowHandle(0)
    browser.SetUserData("width", width)
    browser.SetUserData("height", height)
    browser.WasResized()


def jsCallback(html):
    browser = cefpython.GetBrowserByWindowHandle(0)
    metadata = browser.GetUserData("metadata")
    metadata['final_url'] = browser.GetUrl()
    browser.SetUserData("metadata", metadata)
    browser.SetUserData("html", html)
    cefpython.QuitMessageLoop()


def snap(command, width=800, height=600):
    metadata = {}
    try:
        logging.info("Snapshot url: %s" % command['url'])
        metadata['timestamp'] = int(time())
        metadata['error'] = "0"
        parent_dir = os.path.dirname(command['file'])
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir)
        if 'screen_width' in command:
            width = command['screen_width']
        if 'screen_height' in command:
            height = command['screen_height']
        cefpython.g_debug = False
        commandLineSwitches = dict()
        if 'proxies' in command:
            proxy = command['proxies'][0]
            logging.info("Proxy server: %s:1080" % proxy)
            commandLineSwitches['proxy-server'] = "socks5://" + proxy + ":1080"
        settings = {
            "log_severity": cefpython.LOGSEVERITY_DISABLE,  # LOGSEVERITY_VERBOSE
            "log_file": "",
            "release_dcheck_enabled": False, # Enable only when debugging.
            # This directories must be set on Linux
            "locales_dir_path": cefpython.GetModuleDirectory()+"/locales",
            "resources_dir_path": cefpython.GetModuleDirectory(),
            "multi_threaded_message_loop": False,
            "unique_request_context_per_browser": True,
            "browser_subprocess_path": "%s/%s" % (
                cefpython.GetModuleDirectory(), "subprocess")
        }
        cefpython.Initialize(settings, commandLineSwitches)
        windowInfo = cefpython.WindowInfo()
        windowInfo.SetAsOffscreen(0)
        browserSettings = {"default_encoding": "utf-8"}
        browser = cefpython.CreateBrowserSync(windowInfo, browserSettings, navigateUrl=command['url'])
        cookieManager = cefpython.CookieManager.CreateManager("")
        if 'cookie' in command:
            for k, v in command['cookie'].items():
                cookie = cefpython.Cookie()
                cookie.SetName(k)
                cookie.SetValue(v)
                cookie.SetHasExpires(False)
                cookieManager.SetCookie(command['url'], cookie)
        browser.SetUserData("cookieManager", cookieManager)
        #browser = cefpython.CreateBrowserSync(windowInfo, browserSettings, navigateUrl="about:blank")
        # TODO handle post data
        #req = cefpython.Request.CreateRequest()
        #req.SetUrl(command['url'])
        # req.SetMethod("POST")
        # a = req.SetPostData([])
        #browser.GetMainFrame().LoadRequest(req)

        browser.SendFocusEvent(True)
        browser.SetUserData("width", width)
        browser.SetUserData("height", height)
        browser.SetUserData("metadata", metadata)
        browser.SetClientHandler(ClientHandler(browser, command))
        jsBindings = cefpython.JavascriptBindings(bindToFrames=True, bindToPopups=True)
        jsBindings.SetProperty("delay", int(command['delay']))
        jsBindings.SetProperty("flash_delay", int(command['flash_delay']))
        jsBindings.SetFunction("setPageSize", setPageSize)
        jsBindings.SetFunction("jsCallback", jsCallback)
        jsBindings.SetFunction("log", logging.info)
        browser.SetJavascriptBindings(jsBindings)
        #browser.WasResized()
        cefpython.MessageLoop()
        width = browser.GetUserData("width")
        height = browser.GetUserData("height")
        metadata = browser.GetUserData("metadata")
        image = browser.GetUserData("image")
        html = browser.GetUserData("html")

    except:
        logging.error(sys.exc_info())
        traceback.print_exc()
        metadata['error'] = str(sys.exc_info())
    finally:
        if metadata['error'] == "0":
            metadata['status'] = "OK"
            metadata['finished'] = int(time())
        else:
            metadata['status'] = "error"
            metadata['time_finished'] = int(time())
        cefpython.Shutdown()
        return width, height, image, html, metadata


def get_elements(url, xhtml, tag):
    url = url.rstrip('/')
    elements = xhtml.xpath("//" + tag)

    def get_src(element):
        if 'src' in element.attrib:
            path = element.get('src')
            if path.startswith("http"):
                return path
            else:
                path = path.lstrip('/')
                return url + '/' + path
        else:
            return url
    elements = map(get_src, elements)
    elements = list(OrderedDict.fromkeys(elements))  # remove duplicates
    return elements


def load_command(fpath):
    with open(fpath) as f:
        command = json.load(f)
        command = dict((k, v) for k, v in command.iteritems() if v)  # remove keys with empty values
    types = {'action': str,
             'cookie': str,
             'delay': int,
             'details': int,
             'file': str,
             'flash_delay': int,
             'delay': int,
             'headers': str,
             'height': int,
             'id': int,
             'instance_id': int,
             'priority': int,
             'real_id': int,
             'referer': str,
             'screen_height': int,
             'screen_width': int,
             'server': str,
             'size': str,
             'shot_interval': int,
             'shots': int,
             'timeout': int,
             'url': str,
             'useragent': str,
             'virtual_id': int,
             'width': int}
    command.update({k: types[k](v) if k in types else v for k, v in command.items()})
    if 'cookie' in command:
        command.update({'cookie': dict(map(lambda x: x.split('='), command['cookie'].split(';')))})
    if 'headers' in command:
        command.update({'headers': dict(map(lambda x: x.split(': '), command['headers'].split('\n')))})
    if 'referer' in command:
        if 'headers' in command:
            command['headers']['Referer'] = command['referer']
        else:
            command['headers'] = {'Referer': command['referer']}
    if 'proxies' in command:
        if type(command['proxies']) != list:
            raise Exception("proxies must be a list in command json")

    return command


def main():
    if len(sys.argv) < 2:
        print_usage()
        sys.exit(0)
    command = load_command(os.path.abspath(sys.argv[1]))
    pool = multiprocessing.pool.ThreadPool(processes=1)
    metadata = dict()
    html = None
    image = None
    async_result = pool.apply_async(snap, (command,))
    try:
        timeout = command['timeout'] if 'timeout' in command else 60
        logging.info("Setting timeout at %ds" % timeout)
        width, height, image, html, metadata = async_result.get(timeout=timeout)

    except multiprocessing.TimeoutError:
        logging.error("Timeout")
        metadata['status'] = "error"
        metadata['error'] = "Timeout"
        metadata['time_finished'] = int(time())
    except:
        logging.error(sys.exc_info())
        traceback.print_exc()
        metadata['error'] = str(sys.exc_info())
    finally:
        basename = os.path.splitext(command['file'])[0]
        fpath = {'png': basename + ".png",
                 'html': basename + ".html",
                 'metadata': basename + ".finished"}
        error = metadata['error'] != "0"
        if error:
            logging.error(metadata['error'])
        if html:
            if 'details' in command and command['details'] == 3:
                url = metadata['final_url']
                xhtml = lxml.html.document_fromstring(html)
                metadata['images'] = get_elements(url, xhtml, 'img')
                metadata['scripts'] = get_elements(url, xhtml, 'script')
                metadata['stylesheets'] = get_elements(url, xhtml, 'link[@rel="stylesheet"]')
                metadata['embeds'] = get_elements(url, xhtml, 'embed')
                metadata['applets'] = get_elements(url, xhtml, 'applet')
                metadata['iframes'] = get_elements(url, xhtml, 'iframe')
            logging.info("Saving html: %s" % fpath['html'])
            if 'html' in command and command['html'] == 1:
                save_html(fpath['html'], html)
        if metadata:
            logging.info("Saving metadata: %s" % fpath['metadata'])
            command.update(metadata)
            save_json(fpath['metadata'], command)
        if error:
            sys.exit(1)
        if image:
           logging.info("Saving snapshot (%dx%d): %s" % (width, height, fpath['png']))
           save_image(fpath['png'], image, width, height)
        sys.exit(0)

if __name__ == "__main__":
    main()

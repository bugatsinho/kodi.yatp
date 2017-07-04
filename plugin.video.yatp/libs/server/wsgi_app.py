# coding: utf-8
# Module: wsgi_app
# Created on: 23.07.2015
# Author: Roman Miroshnychenko aka Roman V.M. (romanvm@yandex.ua)
# Licence: GPL v.3: http://www.gnu.org/copyleft/gpl.html
"""
Torrent streamer WSGI application for Web/JSON interface
"""

import os
import sys
import re
import time
from traceback import format_exc
from cStringIO import StringIO
from json import dumps
from inspect import getmembers, isfunction
import xbmc
import methods
from addon import Addon
from torrenter import Streamer, libtorrent
from timers import Timer, check_seeding_limits, save_resume_data
from onscreen_label import OnScreenLabel
from utilities import get_mime

monitor = xbmc.Monitor()
addon = Addon()
_ = addon.initialize_gettext()

sys.path.append(os.path.join(addon.path, 'site-packages'))
from bottle import (route, default_app, request, template, response,
                    static_file, TEMPLATE_PATH, HTTPError, HTTPResponse)

app = default_app()

# Torrent client parameters
resume_dir = os.path.join(addon.config_dir, 'torrents')
if not os.path.exists(resume_dir):
    os.mkdir(resume_dir)
# Initialize torrent client
torrent_client = Streamer(addon.torrent_port,
                          addon.torrent_port + 10,
                          addon.persistent,
                          resume_dir)
torrent_client.set_session_settings(download_rate_limit=addon.dl_speed_limit * 1024,
                                    upload_rate_limit=addon.ul_speed_limit * 1024,
                                    connections_limit=addon.connections_limit,
                                    half_open_limit=addon.half_open_limit,
                                    unchoke_slots_limit=addon.unchoke_slots_limit,
                                    connection_speed=addon.connection_speed,
                                    file_pool_size=addon.file_pool_size)
if not addon.enable_encryption:
    torrent_client.set_encryption_policy(2)
# Timers
if addon.enable_limits:
    limits_timer = Timer(15, check_seeding_limits, torrent_client)
if addon.persistent:
    save_resume_timer = Timer(180, save_resume_data, torrent_client)
# Bottle WSGI application
static_path = os.path.join(addon.path, 'resources', 'web')
TEMPLATE_PATH.insert(0, os.path.join(static_path, 'templates'))
# debug(True)


def serve_file_from_torrent(file_, byte_position, torrent_handle, start_piece, piece_length, oncreen_label):
    """
    Serve a file from torrent by pieces

    This iterator function serves a video file being downloaded to Kodi piece by piece.
    If some piece is not downloaded, the function prioritizes it
    and then waits until it is downloaded.

    :param file_: file object to be server.
    :param byte_position: the start byte
    :param torrent_handle: streamed torrent's handle
    :param start_piece: file's start piece
    :param piece_length: piece length in bytes
    :param oncreen_label: on_screen_label instance to show waiting status
    """
    paused = False  # Needed to prevent unpausing video paused by a user.
    start_time = -1
    with file_:
        while not monitor.abortRequested():
            current_piece = start_piece + int(float(byte_position) / piece_length)
            addon.log_debug('Checking piece #{0}'.format(current_piece))
            # Wait for the piece if it is not downloaded
            while not torrent_handle.have_piece(current_piece):
                if torrent_handle.piece_priority(current_piece) < 7:
                    torrent_handle.piece_priority(current_piece, 7)
                if start_time == -1:
                    start_time = time.time()
                # If a piece is missing, wait for 5 sec. before pausing video
                # This prevents frequent pausing in case of missing pieces
                if time.time() - start_time > addon.pause_timeout and not xbmc.getCondVisibility('Player.Paused'):
                    xbmc.executebuiltin('Action(Pause)')
                    paused = True
                    addon.log_debug('Paused to wait for piece #{0}.'.format(current_piece))
                if paused:
                    oncreen_label.text = _('Waiting for piece #{0}. DL speed: {1}KB/s.').format(
                        current_piece,
                        torrent_handle.status().download_payload_rate / 1024)
                    oncreen_label.show()
                addon.log_debug('Waiting for piece #{0}...'.format(current_piece))
                if monitor.waitForAbort(0.5):
                    break
            if start_time != -1:
                addon.log_debug('Piece #{0} downloaded.'.format(current_piece))
                start_time = -1
            if paused:
                xbmc.executebuiltin('Action(Play)')
                paused = False
                addon.log_debug('Continue playback.')
                oncreen_label.hide()
            file_.seek(byte_position)
            chunk = file_.read(piece_length)
            if not chunk:
                del oncreen_label
                break
            addon.log_debug('Serving piece #{0}'.format(current_piece))
            yield chunk
            byte_position += piece_length


def reset_sliding_window(streamed_file, file_path, start_pos):
    """
    Reset slidkng window to a new position

    :param streamed_file:
    :param file_path:
    :param start_pos:
    :return: generator function
    """
    onscreen_label = OnScreenLabel()
    start_piece = streamed_file['start_piece'] + start_pos / streamed_file['piece_length']
    addon.log_debug('Start piece: {0}'.format(start_piece))
    addon.log_debug('Streamed file: {0}'.format(str(streamed_file)))
    if start_pos > 0:
        addon.log_debug('Resetting sliding window start to piece #{0}'.format(start_piece))
        torrent_client.start_sliding_window_async(streamed_file['torr_handle'],
                                                  start_piece,
                                                  start_piece + addon.sliding_window_length - 1,
                                                  streamed_file['end_piece'] - streamed_file['end_offset'] - 1)
        # Wait until a specified number of pieces after a jump point are downloaded.
        end_piece = min(start_piece + streamed_file['buffer_length'], streamed_file['end_piece'])
        while not torrent_client.check_piece_range(streamed_file['torr_handle'], start_piece, end_piece):
            percent = int(100 * float(torrent_client.sliding_window_position - start_piece) /
                          (end_piece - start_piece))
            onscreen_label.text = _('Buffering torrent: {0}%. DL speed: {1}KB/s').format(
                percent,
                streamed_file['torr_handle'].status().download_payload_rate / 1024)
            onscreen_label.show()
            xbmc.sleep(500)  # xbmc.sleep works better here
        onscreen_label.hide()
    addon.log_debug('Starting file chunks serving...')
    return serve_file_from_torrent(open(file_path, 'rb'),
                                   start_pos,
                                   streamed_file['torr_handle'],
                                   streamed_file['start_piece'],
                                   streamed_file['piece_length'],
                                   onscreen_label)


@route('/')
def root():
    """
    Display a web-UI

    :return:
    """
    login, password = request.auth or (None, None)
    if addon.pass_protect and (login is None or (login, password) != addon.credentials):
        error = HTTPError(401, 'Access denied')
        error.add_header('WWW-Authenticate', 'Basic realm="private"')
        return error
    else:
        return template('torrents')


@route('/json-rpc', method='GET')
def get_methods():
    """
    Display brief JSON-RPC methods documentation

    :return:
    """
    methods_list = []
    docs_list = []
    for member in getmembers(methods):
        if isfunction(member[1]) and not member[0].startswith('_'):
            methods_list.append(member[0])
            docs_list.append(member[1].__doc__.replace('\n', '<br>'))
    info = methods.__doc__.replace('\n', '<br>')
    return template('methods', methods=methods_list, docs=docs_list, info=info)


@route('/json-rpc', method='POST')
def json_rpc():
    """
    Process JSON-RPC requests

    :return: JSON-RPC reply
    """
    data = request.json
    addon.log_debug('JSON request: {0}'.format(data))
    reply = {'jsonrpc': '2.0', 'id': data.get('id', '1')}
    try:
        reply['result'] = getattr(methods, data['method'])(torrent_client, data.get('params'))
    except AttributeError:
        message = 'Method "{0}" not found'.format(data['method'])
        addon.log_error(message)
        reply['error'] = {'code': -32601, 'message': message}
    except:
        addon.log_error('Error while processing JSON request:\n{0}'.format(format_exc()))
        reply['error'] = {'code': -32000, 'message': 'Internal error!', 'data': format_exc()}
    addon.log_debug('JSON response: {0}'.format(reply))
    return reply


@route('/torrents-json')
def get_torrents():
    """
    Get the list of available torrents with their params wrapped in JSON

    :return:
    """
    response.content_type = 'application/json; charset=UTF-8'
    reply = dumps(torrent_client.get_all_torrents_info())
    return reply


@route('/static/<path:path>')
def get_static(path):
    """
    Serve static files

    :param path: relative path to a static file
    :return:
    """
    return static_file(path, root=static_path)


@route('/add-torrent/<source>', method='POST')
def add_torrent(source):
    """
    Add .torrent file or torrent link

    :param source: 'file' or 'link'
    :return:
    """
    addon.log_debug('Adding torrent. Form data: {0}'.format(request.forms.dict))
    if source == 'file':
        buffer_ = StringIO()
        upload = request.files.get('torrent_file')
        upload.save(buffer_)
        torrent = libtorrent.bdecode(buffer_.getvalue())
    else:
        torrent = request.forms.get('torrent_link')
    if request.forms.get('sub_path'):
        path = os.path.join(addon.download_dir, request.forms.get('sub_path'))
    else:
        path = addon.download_dir
    paused = request.forms.get('paused') == 'true'
    torrent_client.add_torrent_async(torrent, path, paused=paused)


@route('/stream/<path:path>')
def stream_file(path):
    """Stream torrent"""
    addon.log_debug('********* Stream Torrent ***********')
    addon.log_debug('Method: ' + request.method)
    addon.log_debug('Headers: ' + str(request.headers.items()))
    if sys.platform == 'win32':
        path = path.decode('utf-8')
    file_path = os.path.normpath(os.path.join(addon.download_dir, path))
    # Workaround to fix unicode path problem on different OSs
    try:
        addon.log_debug('File path: {0}'.format(file_path.encode('utf-8')))
    except UnicodeDecodeError:
        addon.log_debug('File path: {0}'.format(file_path))
    size = os.path.getsize(file_path)
    addon.log_debug('File size: {0}'.format(size))
    mime = get_mime(file_path)
    headers = {'Content-Type': mime,
               'Content-Length': str(size),
               'Accept-Ranges': 'bytes'}
    if request.method == 'GET':
        range_match = re.search(r'^bytes=(\d*)-(\d*)$', request.get_header('Range'))
        start_pos = int(range_match.group(1) or 0)
        end_pos = int(range_match.group(2) or size - 1)
        addon.log_debug('Getting requested range {0}-{1}'.format(start_pos, end_pos))
        if start_pos >= size or end_pos >= size:
            addon.log_error('Error 416, Requested Range Not Satisfiable')
            return HTTPError(416, 'Requested Range Not Satisfiable')
        response_status = 206
        headers['Content-Range'] = 'bytes {0}-{1}/{2}'.format(start_pos, end_pos, size)
        content_length = end_pos - start_pos + 1
        headers['Content-Length'] = str(content_length)
        streamed_file = torrent_client.streamed_file_data
        if (str(streamed_file['torr_handle'].status().state) == 'seeding' or
                    content_length < streamed_file['piece_length'] * (streamed_file['end_offset'] + 1)):
            addon.log_debug('Torrent is being seeded or the end piece requested.')
            # If the file is being seeded or Kodi checks the end piece,
            # then serve the file via Bottle.
            return static_file(path, root=addon.download_dir, mimetype=mime)
        else:
            body = reset_sliding_window(streamed_file, file_path, start_pos)
    else:
        response_status = 200
        body = ''
    addon.log_debug('Reply headers: {0}'.format(headers))
    return HTTPResponse(body, status=response_status, **headers)

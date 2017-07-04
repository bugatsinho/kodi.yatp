# coding: utf-8
# Module: server
# Created on: 01.07.2015
# Author: Roman Miroshnychenko aka Roman V.M. (romanvm@yandex.ua)
# License: GPL v.3 https://www.gnu.org/copyleft/gpl.html
"""
Torrent streamer WSGI server
"""

import xbmc
monitor = xbmc.Monitor()
if monitor.waitForAbort(2.0):
    raise SystemExit

import xbmcgui
from libs.server.wsgi_server import create_server
from simpleplugin import Addon

addon = Addon()
_ = addon.initialize_gettext()
addon.log_notice('Starting Torrent Server...')

# A monkey-patch to set the necessary librorrent version
librorrent_addon = Addon('script.module.libtorrent')
orig_custom_version = librorrent_addon.get_setting('custom_version', False)
orig_set_version = librorrent_addon.get_setting('set_version', False)
librorrent_addon.set_setting('custom_version', 'true')
if addon.libtorrent_version == '1.0.9':
    librorrent_addon.set_setting('set_version', '4')
elif addon.libtorrent_version == '1.1.0':
    librorrent_addon.set_setting('set_version', '5')
elif addon.libtorrent_version == '1.1.1':
    librorrent_addon.set_setting('set_version', '6')
else:
    librorrent_addon.set_setting('set_version', '0')

from libs.server import wsgi_app

librorrent_addon.set_setting('custom_version', orig_custom_version)
librorrent_addon.set_setting('set_version', orig_set_version)
# ======

if addon.enable_limits:
    wsgi_app.limits_timer.start()
if addon.persistent:
    wsgi_app.save_resume_timer.start()
httpd = create_server(wsgi_app.app, port=addon.server_port)
httpd.timeout = 0.1
start_trigger = True
while not monitor.abortRequested():
    httpd.handle_request()
    if start_trigger:
        addon.log_notice('Torrent Server started')
        xbmcgui.Dialog().notification('YATP', _('Torrent server started.'), addon.icon, 3000, False)
        start_trigger = False
addon.log_debug('Stopping Torrent Server...')
addon.log_debug('Close server socket')
httpd.socket.close()
addon.log_debug('Abort buffering')
wsgi_app.torrent_client.abort_buffering()
addon.log_debug('Stop timers')
if addon.enable_limits:
    wsgi_app.limits_timer.abort()
if addon.persistent:
    wsgi_app.save_resume_timer.abort()
addon.log_notice('Torrent Server stopped')

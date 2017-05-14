# coding: utf-8
# Module: utilities
# Created on: 21.08.2015
# Author: Roman Miroshnychenko aka Roman V.M. (romanvm@yandex.ua)
# Licence: GPL v.3: http://www.gnu.org/copyleft/gpl.html

import os
import sys
from mimetypes import guess_type
from cStringIO import StringIO
from xbmcvfs import File
from addon import Addon

addon = Addon()
sys.path.append(os.path.join(addon.path, 'site-packages'))
from hachoir_metadata import extractMetadata
from hachoir_parser import guessParser
from hachoir_core.stream.input import InputIOStream
from hachoir_core.error import HachoirError

MIME = {'.mkv': 'video/x-matroska',
        '.mp4': 'video/mp4',
        '.avi': 'video/avi',
        '.ts': 'video/MP2T',
        '.m2ts': 'video/MP2T',
        '.mov': 'video/quicktime',
        '.wmv': 'video/x-ms-wmv'}


def _parse_file(filename):
    """Extract metatata from file"""
    # Workaround to fix unicode path problem on different OSs
    if sys.platform == 'win32':
        f = open(filename, 'rb')
    else:
        f = File(filename)
    try:
        s = StringIO(f.read(1024 * 64))
        p = guessParser(InputIOStream(s, filename=unicode(filename), tags=[]))
        metadata = extractMetadata(p)
    finally:
        f.close()
    return metadata


def get_duration(filename):
    """
    Get videofile duration in seconds

    :param filename:
    :return: duration
    :raises HachoirError: if video duration cannot be determined
    """
    metadata = _parse_file(filename)
    if metadata is not None and metadata.getItem('duration', 0) is not None:
        duration = metadata.getItem('duration', 0).value
        return duration.seconds + 0.000001 * duration.microseconds
    else:
        raise HachoirError('')  # The exception takes 1 parameter


def get_mime(filename):
    """Get mime type for filename"""
    mime = MIME.get(os.path.splitext(filename)[1])
    if mime is None:
        mime = guess_type(filename, False)[0]
    if mime is None:
        mime = 'application/octet-stream'
    return mime

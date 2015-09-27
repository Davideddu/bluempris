#!/usr/bin/env python
# -*- coding: utf-8 -*-

from dbus.mainloop.glib import DBusGMainLoop
import dbus
import dbus.service
import glib
import logging
import os
import subprocess
import sys
import tempfile
import uuid

if "--do-nothing" in sys.argv:
    raise SystemExit

# logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger()
# logger.setLevel(logging.DEBUG)

MPRIS_BUS_NAME     = 'org.mpris.MediaPlayer2.' # + unique device identifier
MPRIS_OBJ_PATH     = '/org/mpris/MediaPlayer2'
MPRIS_ROOT_IFACE   = 'org.mpris.MediaPlayer2'
MPRIS_PLAYER_IFACE = 'org.mpris.MediaPlayer2.Player'

BT_BUS_NAME        = "org.bluez"
BT_PLAYER_IFACE    = "org.bluez.MediaPlayer1"
BT_DEVICE_IFACE    = "org.bluez.Device1"

DESKTOP_ENTRY = """[Desktop Entry]
Version=1.0
Name={name}
GenericName=Bluetooth media player
Comment=A deamon that exposes any connected Bluetooth device's media controls through MPRIS
Exec=bluempris --do-nothing
Icon={icon}
Terminal=false
Type=Application
Categories=AudioVideo;Player;Recorder;
Keywords=Player;Audio;
NotShowIn=KDE;GNOME;Unity;XFCE;LXDE;
"""

class BlueMPRIS(dbus.service.Object):
    def __init__(self, mplayer_path):
        self._mplayer_p = mplayer_path
        # Should be btdev_BT_MA_CA_DD_RE_SS_playerX
        self.name = "bt" + os.path.basename(os.path.dirname(mplayer_path)).replace("_", "") + os.path.basename(mplayer_path)
        logger.info('MPRIS server for {0} requested'.format(self.name))

        bus = dbus.SystemBus()
        devproxy = bus.get_object(BT_BUS_NAME, os.path.dirname(mplayer_path))
        self._device = dbus.Interface(devproxy, BT_DEVICE_IFACE)
        self._device_props = dbus.Interface(devproxy, dbus.PROPERTIES_IFACE)

        playerproxy = bus.get_object(BT_BUS_NAME, mplayer_path)
        self._player = dbus.Interface(playerproxy, BT_PLAYER_IFACE)
        self._player_props = dbus.Interface(playerproxy, dbus.PROPERTIES_IFACE)

        bus.add_signal_receiver(self._update_props, signal_name="PropertiesChanged")

        self.properties = {MPRIS_ROOT_IFACE: self._get_root_iface_properties(),
                           MPRIS_PLAYER_IFACE: self._get_player_iface_properties()}
        self.bus_name = dbus.service.BusName(MPRIS_BUS_NAME + self.name, bus=dbus.SessionBus())

        dbus.service.Object.__init__(self, self.bus_name, MPRIS_OBJ_PATH)

    def _update_props(self, interface=None, new_props=None, inv_props=None):
        if str(interface) != BT_PLAYER_IFACE:
            return
        logger.debug('_update_props(%s, %s) called', repr(new_props), repr(inv_props))

        mpris_props = dbus.Dictionary({}, signature="sv")
        if "Repeat" in new_props:
            mpris_props["LoopStatus"] = self.get_LoopStatus()
        if "Shuffle" in new_props:
            mpris_props["Shuffle"] = self.get_Shuffle()
        if "Status" in new_props:
            mpris_props["PlaybackStatus"] = self.get_PlaybackStatus()
        if "Position" in new_props:
            mpris_props["Position"] = self.get_Position()
        if "Track" in new_props:
            mpris_props["Metadata"] = self.get_Metadata()

        self.PropertiesChanged(MPRIS_PLAYER_IFACE, mpris_props, dbus.Array([], signature='s'))


    def _get_root_iface_properties(self):
        return {'CanQuit': (True, None),
                'Fullscreen': (False, None),
                'CanSetFullscreen': (False, None),
                'CanRaise': (True, None),
                'HasTrackList': (False, None),
                'Identity': (self._get_device_name, None),
                'DesktopEntry': (self.name, None),
                'SupportedUriSchemes': (dbus.Array([], signature='s'), None),
                'SupportedMimeTypes': (dbus.Array([], signature='s'), None)}

    def _get_player_iface_properties(self):
        return {'PlaybackStatus': (self.get_PlaybackStatus, None),
                'LoopStatus': (self.get_LoopStatus, self.set_LoopStatus),
                'Rate': (1.0, self.set_Rate),
                'Shuffle': (self.get_Shuffle, self.set_Shuffle),
                'Metadata': (self.get_Metadata, None),
                'Volume': (100.0, self.set_Volume),
                'Position': (self.get_Position, None),
                'MinimumRate': (1.0, None),
                'MaximumRate': (1.0, None),
                'CanGoNext': (True, None),
                'CanGoPrevious': (True, None),
                'CanPlay': (True, None),
                'CanPause': (True, None),
                'CanSeek': (False, None),
                'CanControl': (True, None)}

    def _get_device_name(self):
        return self._device_props.Get(BT_DEVICE_IFACE, "Name")

    # --- Properties interface

    @dbus.service.method(dbus_interface=dbus.PROPERTIES_IFACE,
                         in_signature='ss', out_signature='v')
    def Get(self, interface, prop):
        logger.debug(
            '%s.Get(%s, %s) called',
            dbus.PROPERTIES_IFACE, repr(interface), repr(prop))
        (getter, _) = self.properties[interface][prop]
        if callable(getter):
            return getter()
        else:
            return getter

    @dbus.service.method(dbus_interface=dbus.PROPERTIES_IFACE,
                         in_signature='s', out_signature='a{sv}')
    def GetAll(self, interface):
        logger.debug(
            '%s.GetAll(%s) called', dbus.PROPERTIES_IFACE, repr(interface))
        getters = {}
        for key, (getter, _) in self.properties[interface].iteritems():
            getters[key] = getter() if callable(getter) else getter
        return getters

    @dbus.service.method(dbus_interface=dbus.PROPERTIES_IFACE,
                         in_signature='ssv', out_signature='')
    def Set(self, interface, prop, value):
        logger.debug(
            '%s.Set(%s, %s, %s) called',
            dbus.PROPERTIES_IFACE, repr(interface), repr(prop), repr(value))
        _, setter = self.properties[interface][prop]
        if setter is not None:
            setter(value)
            self.PropertiesChanged(
                interface, {prop: self.Get(interface, prop)}, [])

    @dbus.service.signal(dbus_interface=dbus.PROPERTIES_IFACE,
                         signature='sa{sv}as')
    def PropertiesChanged(self, interface, changed_properties,
                          invalidated_properties):
        logger.debug(
            '%s.PropertiesChanged(%s, %s, %s) signaled',
            dbus.PROPERTIES_IFACE, interface, changed_properties,
            invalidated_properties)


    # --- Root interface methods

    @dbus.service.method(dbus_interface=MPRIS_ROOT_IFACE)
    def Raise(self):
        logger.debug('%s.Raise called', MPRIS_ROOT_IFACE)
        # Do nothing, as we do not have a GUI

    @dbus.service.method(dbus_interface=MPRIS_ROOT_IFACE)
    def Quit(self):
        logger.debug('%s.Quit called', MPRIS_ROOT_IFACE)
        # Do nothing, as can't quit the remote player


    # --- Player interface methods

    @dbus.service.method(dbus_interface=MPRIS_PLAYER_IFACE)
    def Next(self):
        logger.debug('%s.Next called', MPRIS_PLAYER_IFACE)
        self._player.Next()

    @dbus.service.method(dbus_interface=MPRIS_PLAYER_IFACE)
    def Previous(self):
        logger.debug('%s.Previous called', MPRIS_PLAYER_IFACE)
        self._player.Previous()

    @dbus.service.method(dbus_interface=MPRIS_PLAYER_IFACE)
    def Pause(self):
        logger.debug('%s.Pause called', MPRIS_PLAYER_IFACE)
        self._player.Pause()

    @dbus.service.method(dbus_interface=MPRIS_PLAYER_IFACE)
    def PlayPause(self):
        logger.debug('%s.PlayPause called', MPRIS_PLAYER_IFACE)
        status = self._player_props.Get(BT_PLAYER_IFACE, "Status")
        if status == "playing":
            self._player.Pause()
        else: #elif status in ("paused", "stopped", "forward-seek", "reverse-seek"):
            self._player.Play()

    @dbus.service.method(dbus_interface=MPRIS_PLAYER_IFACE)
    def Stop(self):
        logger.debug('%s.Stop called', MPRIS_PLAYER_IFACE)
        self._player.Stop()

    @dbus.service.method(dbus_interface=MPRIS_PLAYER_IFACE)
    def Play(self):
        logger.debug('%s.Play called', MPRIS_PLAYER_IFACE)
        self._player.Play()

    @dbus.service.method(dbus_interface=MPRIS_PLAYER_IFACE)
    def Seek(self, offset):
        logger.debug('%s.Seek called', MPRIS_PLAYER_IFACE)
        if not self.get_CanSeek():
            logger.debug('%s.Seek not allowed', MPRIS_PLAYER_IFACE)
            return
        # Do nothing as we can't seek

    @dbus.service.method(dbus_interface=MPRIS_PLAYER_IFACE)
    def SetPosition(self, track_id, position):
        logger.debug('%s.SetPosition called', MPRIS_PLAYER_IFACE)
        if not self.get_CanSeek():
            logger.debug('%s.SetPosition not allowed', MPRIS_PLAYER_IFACE)
            return
        # Do nothing as we can't seek

    @dbus.service.method(dbus_interface=MPRIS_PLAYER_IFACE)
    def OpenUri(self, uri):
        logger.debug('%s.OpenUri called', MPRIS_PLAYER_IFACE)
        if not self.get_CanControl():
            # NOTE The spec does not explictly require this check, but guarding
            # the other methods doesn't help much if OpenUri is open for use.
            logger.debug('%s.OpenUri not allowed', MPRIS_PLAYER_IFACE)
            return
        # Do nothing as we can't send local files to the player

    # --- Player interface signals

    @dbus.service.signal(dbus_interface=MPRIS_PLAYER_IFACE, signature='x')
    def Seeked(self, position):
        logger.debug('%s.Seeked signaled', MPRIS_PLAYER_IFACE)
        # Do nothing, as just calling the method is enough to emit the signal.

    # --- Player interface properties

    def get_PlaybackStatus(self):
        status = self._player_props.Get(BT_PLAYER_IFACE, "Status")
        if status in ("playing", "forward-seek", "reverse-seek"):
            return 'Playing'
        elif status == "paused":
            return 'Paused'
        elif status in ("stopped", "error"):
            return 'Stopped'

    def get_LoopStatus(self):
        repeat = self._player_props.Get(BT_PLAYER_IFACE, "Repeat")
        if repeat == "off":
            return "None"
        elif repeat == "singletrack":
            return "Track"
        else:
            return "Playlist"

    def set_LoopStatus(self, value):
        if not self.get_CanControl():
            logger.debug('Setting %s.LoopStatus not allowed', MPRIS_PLAYER_IFACE)
            return
        if value == 'None':
            self._player_props.Set(BT_PLAYER_IFACE, "Repeat", "off")
        elif value == 'Track':
            self._player_props.Set(BT_PLAYER_IFACE, "Repeat", "singletrack")
        elif value == 'Playlist':
            self._player_props.Set(BT_PLAYER_IFACE, "Repeat", "alltracks")

    def set_Rate(self, value):
        if not self.get_CanControl():
            # NOTE The spec does not explictly require this check, but it was
            # added to be consistent with all the other property setters.
            logger.debug('Setting %s.Rate not allowed', MPRIS_PLAYER_IFACE)
            return
        if value == 0:
            self.Pause()

    def get_Shuffle(self):
        return self._player_props.Get(BT_PLAYER_IFACE, "Shuffle") != "off"

    def set_Shuffle(self, value):
        if not self.get_CanControl():
            logger.debug('Setting %s.Shuffle not allowed', MPRIS_PLAYER_IFACE)
            return
        self._player_props.Set(BT_PLAYER_IFACE, "Shuffle", value and "alltracks" or "off")

    def get_Metadata(self):
        track = self._player_props.Get(BT_PLAYER_IFACE, "Track")
        if track is None or len(track.keys()) == 0:
            return {'mpris:trackid': ''}
        else:
            metadata = {'mpris:trackid': str(uuid.uuid4()),
                        'mpris:artUrl': "file:///usr/share/bluempris/default_artwork.png"}
            if "Duration" in track:
                metadata['mpris:length'] = dbus.Int64(track["Duration"] * 1000)
            if "Title" in track:
                metadata['xesam:title'] = track["Title"]
            if "Artist" in track:
                metadata['xesam:artist'] = dbus.Array([track["Artist"]], signature='s')
            if "Album" in track:
                metadata['xesam:album'] = track["Album"]
            if "Genre" in track:
                metadata['xesam:genre'] = track["Genre"]
            if "TrackNumber" in track:
                metadata['xesam:trackNumber'] = track["TrackNumber"]
            return dbus.Dictionary(metadata, signature='sv')

    def set_Volume(self, value):
        if not self.get_CanControl():
            logger.debug('Setting %s.Volume not allowed', MPRIS_PLAYER_IFACE)
            return
        # We can't set volume

    def get_Position(self):
        return self._player_props.Get(BT_PLAYER_IFACE, "Position") * 1000

def gsettings_get(schema, key):
    out = subprocess.check_output(["gsettings", "get", schema, key])
    return eval(out)

def gsettings_set(schema, key, value):
    subprocess.check_output(["gsettings", "set", schema, key, str(value)])

def create_service(path):
    bus = dbus.SystemBus()
    proxy = bus.get_object(BT_BUS_NAME, os.path.dirname(path))
    props = dbus.Interface(proxy, dbus.PROPERTIES_IFACE)

    icon = props.Get(BT_DEVICE_IFACE, "Icon")
    name = props.Get(BT_DEVICE_IFACE, "Name")

    desktop = DESKTOP_ENTRY.format(name=name, icon=icon)
    dname = "bt" + os.path.basename(os.path.dirname(path)).replace("_", "") + os.path.basename(path)
    dpath = os.path.expanduser("~/.local/share/applications/" + dname + ".desktop")

    subprocess.Popen(["xdg-desktop-menu", "forceupdate", "--mode", "user"])

    with open(dpath, "w") as f:
        f.write(desktop)

    return BlueMPRIS(path)

def destroy_service(service):
    try:
        service.remove_from_connection()
    except Exception:
        import traceback; traceback.print_exc();
    try:
        os.unlink(os.path.expanduser("~/.local/share/applications/" + service.name + ".desktop"))
    except OSError:
        pass
    try:
        imp = gsettings_get('com.canonical.indicator.sound', 'interested-media-players')
        for i in imp:
            if service.name in i:
                imp.remove(i)
        gsettings_set('com.canonical.indicator.sound', 'interested-media-players', imp)
    except Exception:
        import traceback; traceback.print_exc()

def get_paths_with_iface(busname, iface):
    bus = dbus.SystemBus()
    manager = dbus.Interface(bus.get_object(busname, "/"),
                "org.freedesktop.DBus.ObjectManager")
    objs = manager.GetManagedObjects()
    for path, ifaces in objs.iteritems():
        if iface in [str(i) for i in ifaces]:
            yield path

def create_destroy_services(services):
    bus = dbus.SystemBus()
    paths = [i for i in get_paths_with_iface(BT_BUS_NAME, BT_PLAYER_IFACE)]

    for path in services:
        if path not in paths:
            destroy_service(services[path])

    for path in paths:
        if path in services:
            continue
        services[path] = create_service(path)

    return True

if __name__ == "__main__":
    dbus_loop = DBusGMainLoop(set_as_default=True)
    #srv = BlueMPRIS("/org/bluez/hci0/dev_AC_22_0B_47_68_46/player0")
    global services
    services = {}
    create_destroy_services(services)
    glib.timeout_add_seconds(1, create_destroy_services, services)
    try:
        loop = glib.MainLoop()
        loop.run()
    except KeyboardInterrupt:
        for i in services.values():
            destroy_service(i)
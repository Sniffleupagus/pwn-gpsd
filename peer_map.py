import logging

import pwnagotchi.plugins as plugins
from pwnagotchi.ui.components import *
import pwnagotchi.ui.fonts as fonts
from pwnagotchi.mesh.peer import Peer

import _thread
from threading import Event

import os
import glob
from datetime import datetime,timedelta
import time
#import json
try:
    import orjson as json
except Exception as e:
    logging.info("Install orjson with pip to get better json performance")
    import json

import random
import hashlib
import base64
from urllib.parse import urlparse,unquote
from cryptography.fernet import Fernet
import prctl

from math import radians, sin, cos, acos

# gpio for buttons to change view
import RPi.GPIO as GPIO

from PIL import Image, ImageDraw, ImageFont

ADV_FIELD='snorlax'

def checkBounds(overall, new):
    if not new:
        return overall

    if not overall:
        overall = [180,90,-180,-90]

    ret = overall
    if isinstance(new, list):
        if len(new) == 2:
            ret[0] = new[0] if new[0] < overall[0] else overall[0]
            ret[1] = new[1] if new[1] < overall[1] else overall[1]
            ret[2] = new[0] if new[0] > overall[2] else overall[2]
            ret[3] = new[1] if new[1] > overall[3] else overall[3]
        elif len(new) == 4:
            ret[0] = new[0] if new[0] < overall[0] else overall[0]
            ret[1] = new[1] if new[1] < overall[1] else overall[1]
            ret[2] = new[2] if new[2] > overall[2] else overall[2]
            ret[3] = new[3] if new[3] > overall[3] else overall[3]
    elif isinstance(new, dict):
        if 'lat' in new and 'lon' in new:
            try:
                ret[0] = new['lon'] if new['lon'] < overall[0] else overall[0]
                ret[1] = new['lat'] if new['lat'] < overall[1] else overall[1]
                ret[2] = new['lon'] if new['lon'] > overall[2] else overall[2]
                ret[3] = new['lat'] if new['lat'] > overall[3] else overall[3]
            except Exception as e:
                logging.info("overall: %s, new: %s: %s" % (overall,new,e))
        else:
            logging.debug("No lat or lon. skipping: %s" % new)
    else:
        logging.info("Unable to process: Overall %d elements. New %d elements:  %s" % (len(overall), len(new), repr(new)))

    return ret

def boxesOverlap(a, b):
    try:
        if not b:
            logging.exception("None type given")
            return False
        
        if len(b) == 2: # b is a point
            if (a[0] <= b[0] and a[2] >= b[0]) and (a[1] <= b[1] and a[3] >= b[1]):
                return True
            else:
                return False
            
        elif len(b) == 4: # b is a box
            if a[0] > b[2] or b[0] > a[2]:
                # boxes are completely to left or right
                return False

            if a[1] > b[3] or b[1] > a[3]:
                # above or below the other
                return False
        else:
            logging.warn("Expected box or point: %s" % (b))
        return True
    except Exception as e:
        logging.exception(e)
        return False

class gpsTrack:
    name = None
    filename = None
    mtime = 0
    points = None
    bounds = None
    visible = True
    zoomToFit = False
    
    def __init__(self, name, filename=None, visible=True, zoomToFit=False):
        self.name = name
        self.visible = visible
        self.zoomToFit = zoomToFit
        self.gpio = None
        self.points = []

        if filename:
            self.loadFromFile(filename)

    def addPoint(self, tpv):
        if 'lat' in tpv and 'lon' in tpv:
            if not self.points:
                self.points = []

            self.points.append(tpv)
            lat = tpv.get('lat')
            lon = tpv.get('lon')
            if not self.bounds:
                self.bounds = [200,200,-200,-200]

            if lon < self.bounds[0]: self.bounds[0] = lon
            if lat < self.bounds[1]: self.bounds[1] = lat
            if lon > self.bounds[2]: self.bounds[2] = lon
            if lat > self.bounds[3]: self.bounds[3] = lat

    def lastPoint(self):
        if not self.points:
            return None
        return self.points[-1]
            
    def loadFromFile(self, filename, ifUpdated=True):
        try:
            if filename and os.path.isfile(filename):
                self.filename = filename
                mtime = os.stat(filename).st_mtime
                if ifUpdated == False or mtime > self.mtime:
                    logging.debug("loading %s" % filename)
                    self.mtime = mtime
                    lines = []
                    with open(filename) as f:
                        lines = [line.rstrip() for line in f]
                    tmp = gpsTrack("temp")
                    for l in lines:
                        try:
                            l = l.strip(",")
                            l = l.strip('\0')
                            tpv = json.loads(l)
                            tmp.addPoint(tpv)
                                
                        except Exception as e:
                            logging.error("- skip line: %s" % e)
                    logging.info("Loaded %s %d steps within %s" % (os.path.basename(filename), len(tmp.points), tmp.bounds))
                    if len(tmp.points):
                        self.points = tmp.points
                        self.bounds = tmp.bounds
                        del tmp
                        return True
                return False
            else:
                logging.warn("No track file: %s" % (filename))
                return False
        except Exception as e:
            logging.exception(e)
        return False

    def reloadFile(self):
        return self.loadFromFile(self.filename)

class Peer_Map(plugins.Plugin, Widget):
    __author__ = 'Sniffleupagus'
    __version__ = '1.0.5'
    __license__ = 'GPL3'
    __description__ = 'Plot gps tracks on pwnagotchi screen'

    def __init__(self):
        self._agent = None
        self._ui = None
        self.password = "Friendship"
        self.fernet = None
        self.ui_elements = []
        self.me = None
        self.tracks = {}
        self.peers = {}
        self.redrawImage = False
        self.image = None
        self.value = None
        self.xy = None
        self.t_dir = None
        self.font = None
        self.touch_info = {}
        self.zoom_multiplier = 1
        self.gpio = None
        self.window_size = None
        self.keep_going = True
        self._worker_thread = None
        self.trigger_redraw = Event()
        self.occupado = False

        self.state = True # this makes it touchable in Touch_UI plugin
        
        self.track_colors=['#00ff00', '#ffff00', '#ff00ff', '#00ffff', '#40ff40', '#ff8080', '#c0c0ff', '#40c080', '#80c040', '#80c080', '#800000', '#404080'] # a bunch of colors
        self.peer_colors=['red', 'blue', 'purple', 'orange', 'brown']

    def haversine_distance(self, ln1, lt1, ln2, lt2):
        from math import radians, sin, cos, acos

        mlat = radians(float(lt1))
        mlon = radians(float(ln1))
        plat = radians(float(lt2))
        plon = radians(float(ln2))

        dist = 6371.01 * acos(sin(mlat)*sin(plat) + cos(mlat)*cos(plat)*cos(mlon - plon))
        logging.info("The distance is %.2fkm." % dist)
        return dist *1000

    def _worker(self):
        try:
            prctl.set_name("peer_map drawer")
        except:
            logging.info("No rename")

        while self.keep_going:
            try:
                self.check_tracks_and_peers()
                if self.trigger_redraw.is_set():
                    self.trigger_redraw.clear()

                    if self.redrawImage:
                        logging.debug("Redrawing image")
                        self.redrawImage = False
                        self.updateImage()
                        logging.debug("Redrawing complete")
                    else:
                        self.trigger_redraw.wait(timeout=1)
                else:
                    logging.debug("timeout")
                    self.trigger_redraw.wait(timeout=1)
            except Exception as e:
                logging.exception("PM_Drawer: %s" % (e))
        logging.info("peer_map out")

    def updateImage(self):
      try:
        if self.occupado or not self._ui:
            return
        self.occupado = True
        logging.debug("Updating")
        w = self.xy[2]-self.xy[0]
        h = self.xy[3]-self.xy[1]

        image = Image.new('RGBA', (w,h), self.bgcolor)

        d = ImageDraw.Draw(image)
        d.fontmode = '1'
        d.rectangle((0,0,w-1,h-1), fill=self.bgcolor, outline='#808080')

        # compute lon/lat boundaries
        if self.me and self.me.bounds:
            bounds = self.me.bounds.copy()
            logging.debug("Me: %s" % (self.me.bounds))
        else:
            bounds = [180,90,-180,-90]   # the whole world
        for f in sorted(self.tracks):
            t = self.tracks[f]
            if t.visible and t.zoomToFit:
                bounds = checkBounds(bounds, t.bounds)
        logging.debug("Track bounds: %s" % (bounds))

        pbounds = [180,90, -180,-90]
        logging.debug("Unpacking peers: %s" % (repr(self.peers)))
        for p,info in self.peers.items():
            try:
                tpv = info['tpv']
                pbounds = checkBounds(pbounds, tpv)
            except Exception as e:
                logging.exception(e)
        logging.debug("Peer bounds: %s" % (pbounds))
        bounds = checkBounds(bounds, pbounds)

        # go one "tick" bigger around the edges
        bounds[2] += 0.0001
        bounds[0] -= 0.0001
        sw = bounds[2] - bounds[0]
        
        bounds[3] += 0.0001
        bounds[1] -= 0.0001
        sh = bounds[3] - bounds[1]

        logging.debug("Final bounds: %s" % (bounds))

        scale = min(w/sw, h/sh) * self.zoom_multiplier    # pixels per map unit
        midpoint = [(bounds[2]+bounds[0])/2, (bounds[3]+bounds[1])/2]
        if self.zoom_multiplier > 1 and self.me.bounds:
            midpoint = [self.me.bounds[0], self.me.bounds[1]]

        map_bbox = [midpoint[0] - (w/2)/scale, midpoint[1] - (h/2)/scale,
                    midpoint[0] + (w/2)/scale, midpoint[1] + (h/2)/scale]

        # draw tracks
        i = 0
        for f in sorted(self.tracks):
            t = self.tracks[f]
            if t.visible and boxesOverlap( map_bbox, t.bounds):
                # visible and overlaps, so plot it
                logging.debug("Plotting %s %s" % (f, t.bounds))
                lp = None
                color = self.track_colors[i % len(self.track_colors)]
                logging.debug("Scale: %s, %s, map box: %s" % (scale, color, map_bbox))
                for p in t.points:
                    x = (p['lon'] - midpoint[0]) * scale + w/2
                    y = (p['lat'] - midpoint[1]) * scale + h/2
                    #logging.info("Point:(%s %s), (%s %s)" % (p['lon'],x, p['lat'],y))
                    d.point((x, h-y), fill = color)
                i += 1

        logging.debug("Drew tracks")
        # draw peers
        i = 1
        for p, info in self.peers.items():
            data = info.get('tpv', {})
                    
            if 'lat' in data and 'lon' in data:
                x = (data['lon'] - midpoint[0]) * scale + w/2
                y = (data['lat'] - midpoint[1]) * scale + h/2
                pc = self.peer_colors[i % len(self.peer_colors)]
                d.ellipse((x-1, h-y-1, x+1, h-y+1), fill=pc)
                tbox = self.font.getbbox(data.get('name', "XXX"))
                xoff = int(0 if x+tbox[2] < w else (w - (x+tbox[2])))
                yoff = int(0 if (y-tbox[3]) > 0 else tbox[3])
                d.text((x+xoff,h-(y+yoff)), data.get('name', "XXX"), fill=pc, font=self.font)
                logging.debug("Plot peer: %s, %s" % (p, data))
                i += 1

        # draw me
        if self.me and self.me.bounds:
            logging.debug("Me: %s" % (self.me.bounds))
            x = (self.me.bounds[0] - midpoint[0]) * scale + w/2
            y = (self.me.bounds[1] - midpoint[1]) * scale + h/2
            d.ellipse((x-1, h-y-1, x+1, h-y+1), fill=self.peer_colors[0])
            tbox = self.font.getbbox("me")
            xoff = int(0 if x+tbox[2] < w else (w - (x+tbox[2])))
            yoff = int(0 if (y-tbox[3]) > 0 else tbox[3]+2)

            logging.debug("Offset: %s %s = %s" % (xoff, yoff, self.color))
            d.text((x+xoff,h-(y+yoff)), "me", fill=self.color, font=self.font)

        # draw legend and grid on full screen
        if self.window_size:
            try:
                dist = self.haversine_distance(map_bbox[0], 0, map_bbox[2], 0)
                units = self.options.get('units', 'metric').lower()
                logging.info("Distance is: %s. Want units %s" % (dist, units))
                if units in ['feet', 'imperial']:
                    dist *= 3.28084 # meters to feet
                    if dist > 5280: # show miles if far
                        dist_text = "width = %0.2f miles, %0.5e degrees" % (dist/5280.0, map_bbox[2]-map_bbox[0])
                        dist_text += "\nheight = %0.2f miles, %0.5e degrees" % (dist * h / w / 5280.0, map_bbox[3]-map_bbox[1])
                    else:
                        dist_text="width = %0.2f feet, %0.5e degrees" % (dist, map_bbox[2]-map_bbox[0])
                        dist_text += "\nheight = %0.2f feet, %0.5e" % (dist * h / w, map_bbox[3]-map_bbox[1])
                else:
                    if dist > 1000: # km or meters
                        dist_text = "width = %0.2f km, %0.5e degrees" % (dist/1000.0, map_bbox[2]-map_bbox[0])
                        dist_text += "\nheight = %0.2f km, %0.5e" % (dist * h / w / 1000.0, map_bbox[3]-map_bbox[1])
                    else:
                        dist_text = "width = %0.2f m, %0.5e degrees" % (dist, map_bbox[2]-map_bbox[0])
                        dist_text += "\nheight = %0.2f m, %0.5e degrees" % (dist * h / w, map_bbox[3]-map_bbox[1])
                d.text((15,15), "%s\nzoom = %s" % (dist_text, self.zoom_multiplier), fill=self.color, font=self.font)
                logging.info("Window %s" % dist_text)
            except Exception as e:
                logging.exception(e)

        self.image = image
      except Exception as e:
          logging.exception(e)
      logging.debug("Updated")
      self.occupado = False

    def draw(self, canvas, drawer):
        if not self.image:
            w = self.xy[2]-self.xy[0]
            h = self.xy[3]-self.xy[1]
            im = Image.new('RGBA', (w,h), self.bgcolor)
            d = ImageDraw.Draw(im)
            d.rectangle((0,0,w-1,h-1), fill=self.bgcolor, outline='#808080')
            d.text((w/2,h/2), "Peer Map", anchor="mm", font=self.font, fill=self.color)
            self.image = im
                        
        if self.image and self.xy:
            try:
                canvas.paste(self.image.convert(canvas.mode), self.xy)
            except Exception as e:
                logging.error("Paste: %s: %s" % (self.xy, e))
                self.image = self.image.resize((self.xy[2]-self.xy[0], self.xy[3]-self.xy[1]))
                canvas.paste(self.image.convert(canvas.mode), self.xy)
                self.redrawImage = True
                self.trigger_redraw.set()


    def on_loaded(self):
      try:
        logging.info("peer_map loaded with options %s" % (self.options))
        self.password = self.options.get('password', None)
        if self.password:
            self.fernet = Fernet(self.generateKey())

        if 'track_colors' in self.options:
            self.track_colors = self.options['track_colors']
        if 'peer_colors' in self.options:
            self.peer_colors = self.options['peer_colors']
      except Exception as e:
          logging.exception(e)

    def on_ready(self, agent):
      try:
        self._agent = agent

        now = datetime.now()
        self.t_dir = self.options.get("track_dir", "/etc/pwnagotchi/pwn_gpsd")

        fname = os.path.join(self.t_dir, "current.txt")
        if os.path.isfile(fname):
            self.me = gpsTrack("current", fname, True, True)
            logging.debug("Read my location: %s" % (self.me.bounds))
            self.redrawImage = True
            self.trigger_redraw.set()

        tracks_fname_fmt = self.options.get("track_fname_fmt", "pwntrack_%Y%m%d.txt")
        n = 0
        i = 0
        while i < 30 and n < self.options.get("days", 3) and self.keep_going:
            fname = (now - timedelta(days=i)).strftime(tracks_fname_fmt)
            logging.debug("Looking for %s" % os.path.join(self.t_dir, fname))
            if os.path.isfile(os.path.join(self.t_dir, fname)):
                t = gpsTrack(fname, os.path.join(self.t_dir, fname), True, True)
                self.tracks[fname] = t
                n += 1
                self.redrawImage = True
            i += 1

        tracks_fname_fmt = self.options.get("track_fname_fmt", "peertrack_%Y%m%d.txt")
        n = 0
        i = 0
        while i < 30 and n < self.options.get("days", 3) and self.keep_going:
            fname = (now - timedelta(days=i)).strftime(tracks_fname_fmt)
            logging.debug("Looking for %s" % os.path.join(self.t_dir, fname))
            if os.path.isfile(os.path.join(self.t_dir, fname)):
                t = gpsTrack(fname, os.path.join(self.t_dir, fname), True, True)
                self.tracks[fname] = t
                n += 1
                self.redrawImage = True
            i += 1

        self.trigger_redraw.set()

        self.gpio = self.options.get("gpio", None)
        if self.gpio:
            try:
                GPIO.setmode(GPIO.BCM)
                for action in ['zoom_in', 'zoom_out']:
                    if action in self.gpio:
                        if action == 'zoom_in':
                            cb = self.zoom_in
                        elif action == 'zoom_out':
                            cb = self.zoom_out
                        else:
                            cb = self.handle_button
                        p = self.gpio[action]
                        logging.info("Setting up %s -> %s" % (action, p))
                        GPIO.setup(p, GPIO.IN, GPIO.PUD_UP)
                        logging.info("Setting event %s -> %s" % (action, p))
                        GPIO.add_event_detect(p, GPIO.FALLING, callback=cb,
                                   bouncetime=800)
                        logging.info("Set up %s on pin %d" % (action, p))
            except Exception as gpio_e:
                logging.exception("Loading GPIO: %s" % gpio_e)
      except Exception as e:
          logging.exception(e)

    def zoom_in(self, channel):
      try:
        if not self._ui:
            return
    
        if self.window_size:
            self.zoom_multiplier *= 2
        else:
            self.window_size = self.xy.copy()
            border = self.options.get('border', 5)
            self.xy = (border, border, self._ui.width()-border, self._ui.height()-border)
        self.redrawImage = True
        self.trigger_redraw.set()
        logging.info("Zoom multiplier = %s" % self.zoom_multiplier)
      except Exception as e:
          logging.exception("Zoom in: %s: %s" % (channel, e))

    def zoom_out(self, channel):
      try:
        if not self._ui:
            return

        if self.zoom_multiplier >1.0:
            self.zoom_multiplier /= 2
        elif self.window_size:
            self.xy = self.window_size.copy()
            self.window_size = None
        self.redrawImage = True
        self.trigger_redraw.set()
        logging.info("Zoom multiplier = %s" % self.zoom_multiplier)        
      except Exception as e:
          logging.exception("Zoom in: %s: %s" % (channel, e))

    def handle_button(self, channel):
        """GPIO button handler. Use channel as index into self.gpio to get config.toml entry for that button"""
        if self.gpio and channel in self.gpio:
            logging.info("Clicked %s: %s" % (channel, self.gpio[channel]))
        else:
            logging.info("Unexpected click: %s, %s" % (channel, self.gpio))

    def generateKey(self):
        if not self.password:
            return None
        else:
            ekey = hashlib.sha256(self.password.encode()).digest()
            return base64.urlsafe_b64encode(ekey)

    # decrypt a message to json object or text, or default value in case failure
    def decrypt_data(self, encrypted_message, default=None):
        """Decrypts a message with a password."""
        if encrypted_message and self.fernet:
            try:
                decrypted_message = self.fernet.decrypt(encrypted_message.encode()).decode()
                try:
                    logging.debug("Decrypted (%s): %s" % (type(decrypted_message).__name__, decrypted_message))
                    return json.loads(decrypted_message)
                except Exception as e:
                    logging.warn("Not JSON: %s, %s" % (e, decrypted_message))
                    return decrypted_message
            except Exception as e2:
                logging.error("Decrypt failed: %s" % (e2))
                return default
        else:
            return default

    def current_touch_status(self):
        return self.touch_info.get('status', {'pressed':False, 'last_press':None})

    def on_touch_press(self, ts, ui, ui_element, touch_data):
        logging.debug("Touch press: %s, %s" % (touch_data, ui_element));

    def on_touch_release(self, ts, ui, ui_element, touch_data):
        logging.info("Touch release: %s, %s" % (touch_data, ui_element));
        if ui_element != "peer_map":
            logging.warn("Touch release but not my element")
            return
    
        if not self.window_size:
            self.zoom_in("touch")
        elif touch_data['point'][0] > 2*ui.width()/3:
            self.zoom_in("touch")
        elif touch_data['point'][0] < ui.width()/3:
            self.zoom_out("touch")
        else:
            self.toggle_fs("touch")
        ui.set("peer_map", time.time())

    def on_unload(self, ui):
        self.keep_going = False
        self.trigger_redraw.set()
        with ui._lock:
            for el in self.ui_elements:
                try:
                    logging.info("Removing %s" % el)
                    ui.remove_element(el)
                except Exception as e:
                    logging.error("Unable to remove %s: %s" % (el, e))
        if self.gpio:
            logging.info("GPIO: %s" % (self.gpio))
            for action,pin in self.gpio.items():
                try:
                    logging.info("removing event from %s" % (pin))
                    GPIO.remove_event_detect(pin)
                    GPIO.cleanup(pin)
                except Exception as e:
                    logging.exception("GPIO cleanup %s: %s" % (pin, e))


    def on_ui_setup(self, ui):
        self._ui = ui
        try:
            self.xy = self.options.get("pos", [100,30,200,100])
            self.color = self.options.get("color", "white")
            self.bgcolor = self.options.get("bgcolor", "black")
            self.font = ImageFont.truetype(self.options.get('font', "DejaVuSansMono"),
                                           self.options.get('font_size', 10))

            with ui._lock:
                ui.add_element('peer_map', self)
                self.ui_elements.append('peer_map')
                base_pos = self.options.get('pos', [0,55])
                for field in self.options.get('fields', ['fix', 'lon', 'lat', 'alt', 'speed']):
                    fname = "pm_%s" % field
                    pos = (base_pos[0], base_pos[1])
                    ui.add_element(fname, LabeledValue(color="black", label=field, value='---.----',
                                                       position=pos,
                                                       label_font=fonts.BoldSmall, text_font=fonts.Small),
                               )
                    self.ui_elements.append(fname)
                    base_pos[1] += 10
            self._worker_thread = _thread.start_new_thread(self._worker, ())
        except Exception as e:
            logging.exception(e)

    def update_peers(self):
      try:
        ret = False
        agent = self._agent
        if not agent:
            return ret
        for id, p in agent._peers.items():
            adv = p.adv
            if ADV_FIELD in adv:
                logging.debug("Decrypting %s: %s" % (adv.get('name','unknown'), adv))
                e_msg = adv.get(ADV_FIELD, None)
                info = self.peers.get(id, {})  # stored peer data
                if e_msg and e_msg != info.get('enc', None):  # only process if message changed
                    try:
                        raw = self.decrypt_data(e_msg)
                        if raw and raw != info.get('raw', ''):  # double check
                            try:
                                data = json.loads(raw)  # read new peer info into a dict
                                if 'lat' in data and 'lon' in data:
                                    logging.debug("Saving PEER %s: %s" % (p.adv.get('name', None), data))
                                    name = p.adv.get('name', "peer")
                                    data['name'] = name
                                    self.peers[id] = {'enc': e_msg, 'raw': raw, 'tpv': data, 'name': name }
                                    ret = True
                            except Exception as e:
                                logging.error("JSON.loads(%s) %s" % (raw, e))
                        else:
                            logging.warn("New encrypt, same raw")
                    except Exception as e:
                        logging.exception(e)
        return ret
      except Exception as e:
          logging.exception(e)
          return ret

    def check_tracks_and_peers(self):
        # check peers
        redrawImage = False
        if self.update_peers():
            logging.info("Peers changed")
            redrawImage = True

        if self.me and self.me.reloadFile():
            logging.info("My location changed")
            redrawImage = True
            
        for f in sorted(self.tracks):
            logging.debug("Checking %s" % f)
            t = self.tracks[f]

            if t.visible and t.reloadFile():
                logging.info("%s CHANGED" % f)
                redrawImage = True

        if redrawImage:
            logging.debug("REDRAW set")
            self.redrawImage = True
            self.trigger_redraw.set()
        return redrawImage

    def on_ui_update(self, ui):
        bounds = [180,90,-180,-90]

        # check me
        if self.me:
            logging.debug("Me updated: %s" % (self.me.bounds))
            self.redrawImage = True
            tpv = self.me.lastPoint()
            fields = self.options.get('fields', ['fix', 'lon', 'lat', 'alt', 'speed'])
            units = self.options.get('units', 'metric')
            if 'fix' in fields:
                mode = tpv.get('mode', 0)
                if mode == 0:
                    fix = '-'
                elif mode == 1:
                    fix = 'T'
                else:
                    fix = "%sD" % mode

                if 'undivided_count' in tpv:
                    fix += "-%d" % (int(tpv['undivided_count'][0]))

                ui.set('pm_fix', fix)

            for f in ['lon', 'lat']:
                if f in fields:
                    fname = "pm_%s" % f
                    fval = tpv.get(f, None)
                    if fval:
                        ui.set(fname, "%9.4f" % fval)

            if 'alt' in fields:
                alt = tpv.get('alt', tpv.get('altMSL', None))
                if alt:
                    if units in ['feet', 'imperial']:
                        alt *= 3.28084
                    ui.set('pm_alt', "%6.2f" % alt)
                else:
                    ui.set('pm_alt', "---.--")

            if 'speed' in fields:
                speed = tpv.get('speed', None)
                if speed:
                    if units in ['feet', 'imperial']:
                        speed *= 2.237
                    elif units == 'kmh':
                        speed *= 3.6

                    ui.set('pm_speed', "%6.2f" % speed)
                else:
                    ui.set('pm_speed', "---.--")

        #if self.redrawImage:
        #    self.updateImage()
        
    def on_handshake(self, agent, filename, access_point, client_station):
        try:
            if self.me:
                tpv = self.me.lastPoint()
            elif os.path.isfile("/etc/pwnagotchi/pwn_gpsd/current.txt"):
                with open("/etc/pwnagotchi/pwn_gpsd/current.txt", 'r') as f:
                    tpv = json.load(f)
            else:
                tpv = {}
            if 'lat' in tpv:
                gps_filename = filename.replace(".pcap", ".gps.json")
                logging.info(f"saving GPS to {gps_filename} ({tpv})")
                with open(gps_filename, "w+t") as fp:
                    json.dump(tpv, fp)
                    fp.write(",\n")
            else:
                logging.info("not saving GPS. Couldn't find location.")
        except Exception as err:
            logging.exception("[pwn-gpsd handshake] %s" % repr(err))


    def toggle_fs(self, channel):
        if self.window_size:
            self.xy = self.window_size.copy()
            self.window_size = None
            logging.info("Toggle to windowed")
        else:
            self.window_size = self.xy.copy()                    
            border = self.options.get('border', 5)
            self.xy = (border, border, self._ui.width()-border, self._ui.height()-border)
            logging.info("Toggle to fullscreen")
        self.redrawImage = True
        self.trigger_redraw.set()

    def on_webhook(self, path, request):
        try:
            method = request.method
            path = request.path
            logging.info("Webhook %s %s" % (path, repr(request.args)))
            if "/zoom_in" in path:
                self.zoom_in("web")
                return "OK", 204
            elif "/zoom_out" in path:
                self.zoom_out("web")
                return "OK", 204
            elif "/toggle_fs" in path:
                self.toggle_fs("web")
                self.trigger_redraw.set()

                return "OK", 204
            elif "/set_zoom" in path:
                try:
                    logging.info("Args: %s" % (repr(request.args)))
                    zf = int(request.args.get('zf', self.zoom_multiplier))
                    if zf < 1:
                        zf = 1       
                    if zf != self.zoom_multiplier:
                        self.zoom_multiplier = zf
                        self.redrawImage = True
                        self.trigger_redraw.set()
                    return "OK", 204
                
                except Exception as e:
                    logging.exception(e)
                    return "<html><body>%s</body></html>" % (e)
            elif "/set" in path:
                try:
                    logging.info("Setting all settings: %s" % repr(request.args))
                    ret = '<html>><body>PeerMap! %s:<p>' % (path)
                    ret += '<form action="/plugins/peer_map/set" method=get">'
                    ret += '<ul>\n'

                    allowed_options={"units:string":"feet|imperial|metric",
                                     "days:int":"^[0-9]+$"}
                    for a in request.args:
                        if ':' in a:
                            o, t = a.split(":")
                            logging.info("args %s(%s) = %s" % (a, t, request.args[a]))
                            if a in allowed_options:
                                logging.info("\tUpdating %s %s -> %s" % (a, self.options[o], request.args[a]))
                                if t == "int":
                                    self.options[o] = int(request.args[a])
                                elif t == "float":
                                    self.options[o] = float(request.args[a])
                                elif t == "bool":
                                    if request.args[a].lower() == "true":
                                        self.options[o] = True
                                    elif request.args[a].lower() == "false":
                                        self.options[o] = False
                                    else:
                                        logging.error("Invalid boolean value")
                                elif t == "str":
                                    self.options[o] = request.args[a]
                                else:
                                    logging.info("Unsupported options")

                    # zoom multiplier
                    if 'zf' in request.args:
                        try:
                            zm = int(request.args['zf'])
                            self.zoom_multiplier = zm
                            self.redrawImage = True
                        except Exception as e:
                            ret += "<li>Error on zoom multiplier: %s" % e
                            
                    ret += '<li>Zoom Factor<input type=number id="zf" name="zf" min="1" max=2000" value="%d" />\n' % self.zoom_multiplier

                    for o in self.options:
                        logging.info("option %s -> %s" % (o, self.options[o]))
                        t = type(o).__name__
                        if t in ["int", "float", "str", "bool"]:
                            ret += '<li>%s<input type=text name="%s:%s" value="%s">\n' % (o, o, t, self.options[o])

                    ret += "</ul><input type=submit name=Update value=Update></form>"
                    ret += "</body></html>"
                    return ret
                except Exception as e:
                    logging.exception(e)
                    ret += "<p><h3>ERROR: %s</h3></body></html>" % e
                    return ret
            else:
                return("<html><body>PeerMap! %s:<p>Request " % (path)
                       + "<a href=\"/plugins/peer_map/zoom_in\">/plugins/peer_map/zoom_in</a>"
                       + " <a href=\"/plugins/peer_map/zoom_out\">/plugins/peer_map/zoom_out</a>"
                       + " <a href=\"/plugins/peer_map/toggle_fs\">/plugins/peer_map/toggle_fs</a>"
                       + "</body></html>")
        except Exception as e:
            logging.exception(e)
            return "<html><body>Error! %s</body></html>" % (e)


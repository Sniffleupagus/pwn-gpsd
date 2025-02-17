import logging

import pwnagotchi.plugins as plugins
from pwnagotchi.ui.components import *
import pwnagotchi.ui.fonts as fonts
from pwnagotchi.mesh.peer import Peer

import os
import glob
from datetime import datetime,timedelta
import time
import json
import random
import hashlib
import base64
from urllib.parse import urlparse,unquote
from cryptography.fernet import Fernet

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
        logging.info("Unexpected size: overall %d, new %d (not 2 or 4) %s" % (len(overall), len(new), repr(new)))

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

    def loadFromFile(self, filename, ifUpdated=True):
        try:
            if filename and os.path.isfile(filename):
                self.filename = filename
                mtime = os.stat(filename).st_mtime
                if ifUpdated == False or mtime > self.mtime:
                    logging.debug("loading %s" % filename)
                    self.mtime = mtime
                    with open(filename) as f:
                        tmp = gpsTrack("temp")
                        lines = [line.rstrip() for line in f]
                        for l in lines:
                            try:
                                l = l.strip(",")
                                l = l.strip('\0')
                                tpv = json.loads(l)
                                tmp.addPoint(tpv)
                                
                            except Exception as e:
                                logging.error("- skip line: %s" % e)
                        logging.debug("Loaded %s %d steps within %s" % (os.path.basename(filename), len(tmp.points), tmp.bounds))
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

class peerMapImage(Widget):
    image = None
    position = None
    tracks = {}
    
    def __init__(self, position, color='white', bgcolor='black', *, font=None, tracks={}):
        
        self.position = position
        self.color=color
        self.bgcolor=color

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
        self.image = None
        self.position = None
        self.t_dir = None
        self.font = None
        
        self.track_colors=['#00ff00', '#40ff40', '#80FF80', '#c0ffc0', '#00c000', '#40c040', '#80c080', '#008000', '#408040'] # a bunch of greens
        self.peer_colors=['red', 'blue', 'purple', 'orange', 'brown']


    def updateImage(self):
        w = self.position[2]-self.position[0]
        h = self.position[3]-self.position[1]
        
        image = Image.new('RGBA', (w,h), self.bgcolor)

        d = ImageDraw.Draw(image)
        d.fontmode = '1'
        d.rectangle((0,0,w-1,h-1), fill=self.bgcolor, outline='#808080')

        # compute lon/lat boundaries
        if self.me:
            bounds = self.me.bounds.copy()
            logging.debug("Me: %s" % (self.me.bounds))
        else:
            bounds = [180,90,-180,-90]
        for f in sorted(self.tracks):
            t = self.tracks[f]
            if t.visible and t.zoomToFit:
                bounds = checkBounds(bounds, t.bounds)
        logging.debug("Track bounds: %s" % (bounds))

        pbounds = [180,90, -180,-90]
        logging.debug("Unpacking peers: %s" % (repr(self.peers)))
        for p,tpv in self.peers.items():
            pbounds = checkBounds(pbounds, tpv)
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

        scale = min(w/sw, h/sh)    # pixels per map unit
        midpoint = [(bounds[2]+bounds[0])/2, (bounds[3]+bounds[1])/2]
        map_bbox = [midpoint[0] - (w/2)/scale, midpoint[1] - (h/2)/scale,
                    midpoint[0] + (w/2)/scale, midpoint[1] + (h/2)/scale]
            
        # draw tracks
        i = 0
        for f in sorted(self.tracks):
            t = self.tracks[f]
            if t.visible:  #and boxesOverlap( map_bbox, t.bounds):
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

        # draw peers
        i = 1
        for p, tpv in self.peers.items():
            logging.debug("PEER info tpv: %s, %s" % (type(tpv), tpv))
            if isinstance(tpv, str):
                try:
                    data = json.loads(tpv)
                except Exception as e:
                    logging.exception("Error reading json: %s" % e)
            if 'lat' in data and 'lon' in data:
                x = (data['lon'] - midpoint[0]) * scale + w/2
                y = (data['lat'] - midpoint[1]) * scale + h/2
                d.ellipse((x-1, h-y-1, x+1, h-y+1), fill=self.peer_colors[i % len(self.peer_colors)])
                tbox = self.font.getbbox(data.get('name', "XXX"))
                xoff = int(0 if x+tbox[2] < w else (w - (x+tbox[2])))
                yoff = int(0 if (y-tbox[3]) > 0 else tbox[3])
                d.text((x+xoff,h-(y+yoff)), data.get('name', "XXX"), color=self.color, font=self.font)
                logging.debug("Plot peer: %s, %s" % (p, tpv))

        # draw me
        if self.me:
            logging.debug("Me: %s" % (self.me.bounds))
            x = (self.me.bounds[0] - midpoint[0]) * scale + w/2
            y = (self.me.bounds[1] - midpoint[1]) * scale + h/2
            d.ellipse((x-1, h-y-1, x+1, h-y+1), fill=self.peer_colors[0])
            tbox = self.font.getbbox("me")
            xoff = int(0 if x+tbox[2] < w else (w - (x+tbox[2])))
            yoff = int(0 if (y-tbox[3]) > 0 else tbox[3])

            logging.debug("Offset: %s %s" % (xoff, yoff))
            d.text((x+xoff,h-(y+yoff)), "me", color=self.color, font=self.font)
        self.image = image
            
    def draw(self, canvas, drawer):
        if not self.image:
            self.updateImage()
        if self.image and self.position:
            canvas.paste(self.image.convert(canvas.mode), self.position)

    def on_ready(self, agent):
        self._agent = agent

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

        now = datetime.now()
        self.t_dir = self.options.get("track_dir", "/etc/pwnagotchi/pwn_gpsd")
        tracks_fname_fmt = self.options.get("track_fname_fmt", "pwntrack_%Y%m%d.txt")
        n = 0
        i = 0
        while i < 30 and n < self.options.get("days", 5):
            fname = (now - timedelta(days=i)).strftime(tracks_fname_fmt)
            logging.debug("Looking for %s" % os.path.join(self.t_dir, fname))
            if os.path.isfile(os.path.join(self.t_dir, fname)):
                t = gpsTrack(fname, os.path.join(self.t_dir, fname), True, True)
                self.tracks[fname] = t
                n += 1
            i += 1

        fname = os.path.join(self.t_dir, "current.txt")
        if os.path.isfile(fname):
            self.me = gpsTrack("current", fname, True, True)
            logging.debug("Read my location: %s" % (self.me.bounds))
      except Exception as e:
          logging.exception(e)
            
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
                    return json.loads(decrypted_message)
                except Exception as e:
                    logging.warn("Not JSON: %s, %s" % (e, decrypted_message))
                    return decrypted_message
            except Exception as e:
                logging.error("Decrypt failed: %s" % (e))
                return default
        else:
            return default


    def on_unload(self, ui):
        with ui._lock:
            for el in self.ui_elements:
                try:
                    logging.info("Removing %s" % el)
                    ui.remove_element(el)
                except Exception as e:
                    logging.error("Unable to remove %s: %s" % (el, e))

    def on_ui_setup(self, ui):
        self._ui = ui
        try:
            self.position = self.options.get("pos", [100,30,200,100])
            self.color = self.options.get("color", "white"),
            self.bgcolor = self.options.get("bgcolor", "black")
            self.font = ImageFont.truetype(self.options.get('font', "DejaVuSansMono"),
                                           self.options.get('font_size', 10))

            with ui._lock:
                ui.add_element('peer_map', self)
                self.ui_elements.append('peer_map')
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
                tpv = self.decrypt_data(adv.get(ADV_FIELD))
                if tpv and tpv != self.peers.get(id, ""):
                    if isinstance(tpv, str):
                        try:
                            data = json.loads(tpv)
                        except Exception as e:
                            logging.info("Error reading json: %s" % e)
                    if 'lat' in tpv and 'lon' in tpv:
                        logging.debug("Saving PEER %s: %s" % (p.adv.get('name', None), data))
                        data['name'] = p.adv.get('name', "peer")
                        self.peers[id] = json.dumps(data)
                        ret = True
        return ret
      except Exception as e:
          logging.exception(e)
          return ret

    def on_ui_update(self, ui):
        redrawImage = False
        bounds = [180,90,-180,-90]

        # check tracks
        for f in sorted(self.tracks):
            logging.debug("Checking %s" % f)
            t = self.tracks[f]
            if t.visible and t.reloadFile():
                logging.info("%s CHANGED" % f)
                redrawImage = True
        
        # check peers
        if self.update_peers():
            redrawImage = True

        # check me
        if self.me and self.me.reloadFile():
            logging.debug("Me updated: %s" % (self.me.bounds))
            redrawImage = True

        if redrawImage:
            self.updateImage()


        

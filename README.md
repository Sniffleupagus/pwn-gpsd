# pwn-gpsd
Proxy GPSD with pacing for plugins, and share encrypted location over pwngrid mesh

better instructions later:

Make sure gpsd is installed and working properly before continuing.

Edit the service file to make sure the python3 venv path is correct.

Install the service file to /etc/systemd/system

Install pwn-gpsd.py to /usr/bin or /usr/local/bin or somewhere else in root's PATH

 sudo systemctl daemon-reload
 sudo systemctl enable pwn-gpsd
 sudo systemctl start pwn-gpsd

Install the plot_gps.py plugin into the custom plugins directory
   sudo cp plot_gps.py /usr/local/share/pwnagotchi/custom-plugins/

Restart pwnagotchi and enable the plugin.

pwn-gpsd will share location over pwngrid mesh and save track logs in "/etc/pwnagotchi/pwn_gpsd". The files may get big (test file was 1.5M after about 30 hours).  plot_gps plugin will draw a box, and plot itself and other pwnies it sees with relative GPS positions.  If they are in the same room, GPS error is probably larger than the spaces between them, and they will move around the box randomly.  if you get larger distances away, I think it shows their relative positions.

Definitely an early work in progress.

#!/bin/sh
set -eu

config_dir=/tmp/emane
mkdir -p "$config_dir"

cat > "$config_dir/platform.xml" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE platform SYSTEM "file:///usr/share/emane/dtd/platform.dtd">
<platform name="rfpipe-feasibility">
  <param name="controlportendpoint" value="0.0.0.0:47000"/>
  <param name="eventservicedevice" value="eth0"/>
  <param name="eventservicegroup" value="224.1.2.8:45702"/>
  <param name="otamanagerdevice" value="eth0"/>
  <param name="otamanagergroup" value="224.1.2.8:45703"/>
  <nem id="$NEM_ID" definition="nem.xml"/>
</platform>
EOF

cat > "$config_dir/nem.xml" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE nem SYSTEM "file:///usr/share/emane/dtd/nem.dtd">
<nem name="RF Pipe feasibility NEM">
  <transport definition="transvirtual.xml"/>
  <mac definition="rfpipemac.xml"/>
  <phy>
    <param name="fixedantennagain" value="0.0"/>
    <param name="fixedantennagainenable" value="on"/>
    <param name="bandwidth" value="1M"/>
    <param name="noisemode" value="outofband"/>
    <param name="propagationmodel" value="precomputed"/>
    <param name="systemnoisefigure" value="4.0"/>
    <param name="subid" value="2"/>
    <param name="txpower" value="0.0"/>
  </phy>
</nem>
EOF

cat > "$config_dir/transvirtual.xml" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE transport SYSTEM "file:///usr/share/emane/dtd/transport.dtd">
<transport name="RF Pipe feasibility transport" library="transvirtual">
  <param name="bitrate" value="1M"/>
  <param name="devicepath" value="/dev/net/tun"/>
  <param name="device" value="emane0"/>
  <param name="address" value="$EMANE_IP"/>
  <param name="mask" value="255.255.255.0"/>
</transport>
EOF

cat > "$config_dir/rfpipemac.xml" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE mac SYSTEM "file:///usr/share/emane/dtd/mac.dtd">
<mac name="RF Pipe feasibility MAC" library="rfpipemaclayer">
  <param name="enablepromiscuousmode" value="off"/>
  <param name="datarate" value="1M"/>
  <param name="jitter" value="0"/>
  <param name="delay" value="0"/>
  <param name="flowcontrolenable" value="off"/>
  <param name="flowcontroltokens" value="10"/>
  <param name="pcrcurveuri" value="file:///usr/share/emane/xml/models/mac/rfpipe/rfpipepcr.xml"/>
</mac>
EOF

exec emane -l 3 "$config_dir/platform.xml"
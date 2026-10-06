#!/bin/sh
# Trusted project setup, never exposed to model-generated code.
# Only execute inside the separately imported forestry-uav-agent-runtime distro.
set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install --no-install-recommends -y \
  docker.io=29.1.3-0ubuntu3~24.04.2 \
  containerd=2.2.1-0ubuntu1~24.04.3 \
  runc=1.3.4-0ubuntu1~24.04.1 \
  ca-certificates=20260601~24.04.1 \
  iptables=1.8.10-3ubuntu2 \
  libip4tc2=1.8.10-3ubuntu2 libip6tc2=1.8.10-3ubuntu2 \
  libnetfilter-conntrack3=1.0.9-6build1 libnfnetlink0=1.0.2-2build1 \
  libnftables1=1.0.9-1ubuntu0.1 libnftnl11=1.2.6-2build1
systemctl enable --now docker.service
systemctl is-active docker.service
docker --host unix:///var/run/docker.sock version
dpkg-query -W docker.io containerd runc ca-certificates iptables

#!/bin/bash

set -e
set -u

if [[ $# != 1 ]]; then
  echo "Usage: $0 honeytail_version"
  echo " e.g. '$0 1.133' to update to version 1.133"
  exit 1
fi

honeytail_version=$1

TOP=$(dirname $0)/..

rm -f $TOP/honeytail.linux $TOP/honeytail.darwin

platform=$(uname)
case $platform in
  Darwin)
    SHA256SUM="shasum -a 256"
    ;;
  Linux)
    SHA256SUM=sha256sum
    ;;
  *)
    echo "Don't know what sha256sum to use for platform '$platform'"
    exit 1
    ;;
esac

echo fetching linux honeytail
curl -f -L -o $TOP/honeytail.linux https://honeycomb.io/download/honeytail/linux/$honeytail_version

#echo fetching osx honeytail
#curl -f -L -o $TOP/honeytail.darwin https://honeycomb.io/download/honeytail/darwin/$honeytail_version

echo generating sha256s
linux_sha256=$($SHA256SUM $TOP/honeytail.linux | cut -d' ' -f 1)
#darwin_sha256=$($SHA256SUM $TOP/honeytail.darwin | cut -d' ' -f 1)
darwin_sha256=

rm -f $TOP/honeytail.linux $TOP/honeytail.darwin

echo writing honey_installer/honeytail_version.py
cat <<EOF > $TOP/honey_installer/honeytail_version.py
# generated file - do not edit
# instead update ../update_honeytail.sh
import platform
HONEYTAIL_VERSION="$honeytail_version"
HONEYTAIL_CHECKSUM = {
    "Linux": "$linux_sha256",
    #"Darwin": "$darwin_sha256"
}.get(platform.system(), None)
EOF

git commit -m "bump honeytail to $honeytail_version" $TOP/honey_installer/honeytail_version.py
echo committed honeytail_version.py change.  please do not forget to push.

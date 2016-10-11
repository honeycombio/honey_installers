#!/bin/bash

set -e
set -u

build_number=$1

platform=$(uname | tr "[:upper:]" "[:lower:]")
status=0
for name in mysql mongo nginx; do
  expected_version="${name}_installer, version ${build_number}-${platform}"
  version=`../dist/${name}_installer --version 2>/dev/null`
  if [[ $version != $expected_version ]]; then
    echo "${name} failed, output '${version}'.  expected '${expected_version}'"
    status=1
  fi
done

exit $status

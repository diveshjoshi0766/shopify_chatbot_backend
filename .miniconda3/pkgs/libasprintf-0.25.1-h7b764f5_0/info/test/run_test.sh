

set -ex



test -f ${PREFIX}/lib/libasprintf.*.dylib
test ! -f ${PREFIX}/lib/libasprintf$SHLIB_EXT
exit 0

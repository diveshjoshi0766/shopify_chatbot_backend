

set -ex



test -f ${PREFIX}/lib/libintl.*.dylib
test ! -f ${PREFIX}/lib/libintl$SHLIB_EXT
test ! -f ${PREFIX}/include/libintl.h
exit 0

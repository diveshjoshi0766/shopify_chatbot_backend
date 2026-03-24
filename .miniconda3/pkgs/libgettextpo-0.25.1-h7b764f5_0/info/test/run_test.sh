

set -ex



test -f ${PREFIX}/lib/libgettextpo.*.dylib
test ! -f ${PREFIX}/lib/libgettextpo$SHLIB_EXT
exit 0

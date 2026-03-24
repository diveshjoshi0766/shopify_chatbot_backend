

set -ex



mdb_copy -V
mdb_dump -V
mdb_load -V
mdb_stat -V
conda inspect linkages -p $PREFIX lmdb
exit 0

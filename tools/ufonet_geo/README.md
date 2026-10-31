# ufonet_geo (vendored)

A copy of the shared georeferencing package from the Georeferencing repo
(`ufonet_geo/`), at the commit in `VENDORED_VERSION`. The nodes install
from `requirements.txt` over WiFi only and don't fetch GitHub at run time, so
the package is copied here rather than installed. numpy only.

`tools/camera_geometry.py` and `tools/geo_core.py` re-export from it (they
are copies of the Georeferencing shims of the same names).

**Don't edit these files here.** Change the package in Georeferencing, then
refresh the copy from a commit on its main:

```bash
SHA=<commit>
for f in __init__ geometry pose terrain ortho; do
  git -C ../Georeferencing show $SHA:ufonet_geo/$f.py > tools/ufonet_geo/$f.py
done
{ echo "commit $SHA"; (cd tools/ufonet_geo && sha256sum __init__.py geometry.py pose.py terrain.py ortho.py); } \
  > tools/ufonet_geo/VENDORED_VERSION
```

`tests/test_ufonet_geo_vendored.py` fails if a file no longer matches its
recorded hash.

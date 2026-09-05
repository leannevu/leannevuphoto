# Public NordLocker on-demand integration

Tested September 4, 2026 using the public share configured in data/emails.csv.

## Implemented result

The app now lists all 173 photos through NordLocker's browser client and decrypts
individual files or stored thumbnails in memory. The successful Flask test returned
a 33,766-byte JPEG thumbnail and a 241,573-byte larger preview for the first photo.
The live UI test initially requested only four photos, displayed the lightbox,
and submitted the original filename/share link with email delivery mocked.

The bridge calls the client's node listing, thumbnail, and file-decryption services
directly inside an isolated public-share session. It does not open the native
preview viewer, prefetch neighboring originals, or click Download all. The grid
loads at most three thumbnail requests concurrently as cards enter the viewport.
Originals are fetched only for larger previews, final downloads, or a missing
thumbnail fallback. No archive or photo files are saved to the project.

## Earlier viewer-only investigation

- The share opens in a fresh Edge session without a login or security code.
- The gallery contains 173 photos. Only 15 rows appeared in the initial viewport.
- NordLocker reports a 960.6 MB archive for Download all. The import was cancelled
  at the user's request; no gallery archive or imported photos were saved in data/.
- Opening the first photo caused four encrypted original-sized objects to be
  fetched, each about 6 MB. The displayed JPEG was 6,217,125 bytes and
  4024 by 6048 pixels.
- The displayed photo uses a temporary blob URL created within NordLocker's
  browser session. That URL cannot be reused as an image source in this Flask app.
- No lightweight thumbnail requests were observed in this list/preview test.
  This does not prove that NordLocker has no thumbnail capability in other views
  or versions.

The earlier viewer-only test did not exercise the stored-thumbnail service.
That service was subsequently found in the public client and is used by the
implemented bridge. The experimental full-import implementation was removed
after the change in scope.

The read-only probe can be rerun with Playwright and installed Microsoft Edge:

```powershell
python -m pip install --target .test-tools playwright
python probe_nordlocker_previews.py CLIENT_EMAIL
```

The probe opens one preview and observes its network requests. It never clicks
Download all or saves image files. It does transfer the images needed by the viewer.

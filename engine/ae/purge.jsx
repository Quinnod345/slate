/*  purge.jsx: releases After Effects' memory caches (not the disk cache) before a long render with AE open. ES3. */
#include "lib/slate_math.jsx"
#include "lib/slate_ae.jsx"

SL.main(function () {
    app.purge(PurgeTarget.ALL_CACHES);
    SL.put("purged", "ALL_CACHES");
    return "OK";
});

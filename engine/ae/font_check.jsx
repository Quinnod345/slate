/*  font_check.jsx: which font families After Effects can see, for the brand's fonts (by family name). Read-only;
    changes nothing, also reports the open project's state. Args: {families: [..], pattern: "^(A|B)$"}. ES3. */
#include "lib/slate_math.jsx"
#include "lib/slate_ae.jsx"

SL.main(function () {
    var args = SL.args(), re = args.pattern ? new RegExp(args.pattern, "i") : null, groups, g, f, ft, total = 0;
    var families = {}, found = [], want = args.families || [], i, missing = [];
    SL.put("project", SL.projectState());
    groups = app.fonts.allFonts;
    for (g = 0; g < groups.length; g++) {
        for (f = 0; f < groups[g].length; f++) {
            ft = groups[g][f];
            total++;
            if (re && !re.test(ft.familyName)) { continue; }
            if (!families[ft.familyName]) { families[ft.familyName] = []; found.push(ft.familyName); }
            families[ft.familyName].push(ft.styleName + ":" + ft.postScriptName);
        }
    }
    for (i = 0; i < want.length; i++) { if (!families[want[i]]) { missing.push(want[i]); } }
    SL.put("total_fonts", total);
    SL.put("families", families);
    SL.put("missing", missing);
    return "OK";
});

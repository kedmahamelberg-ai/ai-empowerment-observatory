AI Empowerment Observatory — complete public-folder fix

WHY THE BUILD FAILED
--------------------
build_public_site.py requires these root-level public directories:

  edu/
  pro/
  report/
  reports/
  methodology/
  status/
  privacy/

The latest error says methodology/ is missing.

THIS PATCH
----------
Adds/contains the launch public directories that are easy to miss:

  methodology/
    index.html
    methodology.css
    methodology.js

  status/
    index.html
    status.css
    status.js

  report/
    index.html
    report.css
    report.js

  privacy/
    index.html

  reports/
    placeholder.txt

It also contains an improved:
  scripts/build_public_site.py

The improved builder reports ALL missing required public folders/files/data
in a single error instead of failing one item at a time.

INSTALL
-------
At the repository ROOT (same level as edu/, pro/, data/, scripts/):

1. Upload methodology/ with its three files.
2. Upload status/ with its three files.
3. Confirm report/ contains its three files.
4. Confirm privacy/ contains index.html.
5. Confirm reports/ exists and contains placeholder.txt or the generated PDF.
6. Replace scripts/build_public_site.py with the version in this patch.

Commit:
  Complete public Observatory launch folders

Then start a NEW workflow run from the new commit.

EXPECTED ROOT STRUCTURE
-----------------------
edu/
pro/
report/
reports/
methodology/
status/
privacy/
data/
scripts/

If the next build fails, the new preflight message will list every remaining
missing FILE / DIR / DATA requirement at once.

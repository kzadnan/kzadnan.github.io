# kzadnan.github.io

Personal academic website for **Khalid Zobaid Adnan**, Ph.D. candidate in Mechanical Engineering at the University of Utah.

Live URL after GitHub Pages is enabled: [https://kzadnan.github.io](https://kzadnan.github.io)

## Local preview

Open `index.html` in a browser, or from this folder:

```bash
python -m http.server 8000
```

Then visit `http://localhost:8000`.

## Publish on GitHub Pages

This folder is meant to be the user site repository named `kzadnan.github.io`.

```bash
git add .
git commit -m "Add personal academic website"
git branch -M main
git remote add origin https://github.com/kzadnan/kzadnan.github.io.git
git push -u origin main
```

Then on GitHub: **Settings → Pages → Deploy from a branch → `main` / root**.

## Google Search indexing

The site is live at [https://kzadnan.github.io](https://kzadnan.github.io). To appear in Google search results:

1. Open [Google Search Console](https://search.google.com/search-console) and add the property `https://kzadnan.github.io`.
2. Verify ownership (HTML tag or DNS — GitHub Pages works well with the HTML tag method).
3. Submit the sitemap: `https://kzadnan.github.io/sitemap.xml`.
4. Use **URL Inspection** on the homepage and click **Request indexing** to speed up the first crawl.

`robots.txt` and `sitemap.xml` in the repo root help search engines discover the site.

## Contents

- Claim-first homepage: environment-dependent thermal boundary conductance
- Three research themes linked to papers
- Interface schematic with caption
- Device-level relevance: finite-element silicon-on-diamond results tying TBC to hot-spot temperature
- Peer-reviewed publications with full citations and a per-paper contribution statement
- Conference presentations (SHTC 2025 and poster)
- Teaching with course numbers, funding (NSF CAREER CBET-2337749), and expected graduation
- Collaboration and student notes (no implied open positions)

Home address and phone number from the CV are intentionally omitted.

## CV

`cv/index.html` is the source of truth for the CV. Regenerate the PDF after editing it:

```bash
python -m http.server 8766
# in another shell, from the repo root:
chrome --headless --no-pdf-header-footer \
  --print-to-pdf="$PWD/assets/CV_Khalid_Zobaid_Adnan.pdf" \
  http://localhost:8766/cv/index.html
```

Chrome caches aggressively between renders; if the output looks stale, render with a
throwaway `--user-data-dir`.

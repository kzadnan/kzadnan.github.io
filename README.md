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

- About, education, and appointments from the CV
- Research-interest section (successive interfaces, MLIPs, interlayers, WBG/UWBG thermal management, scientific ML, HPC workflows)
- Publication cards with venue, key result, DOI, and arXiv where available
- Open-source software and methods

Home address and phone number from the CV are intentionally omitted.

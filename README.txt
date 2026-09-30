STOCKLENS SITE - how to put it online (free)

1. Create a free GitHub account and a new repository (public is simplest).
2. Upload everything in this folder (index.html, nse_updater.py, .github folder).
3. In the repository: Settings > Pages > Source = "GitHub Actions".
4. Open the Actions tab > "Update data and publish site" > Run workflow.
   The first run downloads about a year of data and can take 10-20 minutes.
5. Your site appears at https://YOUR-NAME.github.io/REPO-NAME/
   After that it updates by itself every weekday evening.

IF THE DOWNLOAD FAILS: NSE sometimes blocks cloud servers. Then run the updater on your own
computer or a small VPS instead (python nse_updater.py), and upload the "out" folder with index.html
to any hosting (Netlify, Cloudflare Pages, or your own server).

STILL SAMPLE DATA: news and filings, per-stock filings, and the fundamentals table.
Before charging users, get NSE permission or licensed data (see the plan on the website).

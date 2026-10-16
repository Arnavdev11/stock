"use strict";
/* Phase 5I helper for the older byte-identity pins. legacy(html) reverses exactly the Phase 5I changes (the global bar markup and CSS, the
   "&section=" routing regex, the Stock Detail render guard, the search mount point and the stocklens-nav module, and Phase 5I's removal of the old top navigation row), so an older test can
   keep asserting that its own blocks are byte-identical to what they were before Phase 5I. test_global_navigation.js proves that
   legacy(page) equals the Phase 5H.6 page (aa4ace1) exactly: nothing else changed. */
const FX = JSON.parse(require("fs").readFileSync(__dirname + "/fixtures/home_before_5j.json", "utf8"));   /* the Phase 5H.6/5I home page, exactly, for the pins that predate Phase 5J */
const NEWRE = '/^#stock=([^&]+)(?:&section=[A-Za-z0-9_-]{1,30})?$/', OLDRE = '/^#stock=([^&]+)$/';
const OLDNAV = "<nav aria-label=\"Main\"><b>StockLens India</b><a href=\"#demo\">Demo</a><a href=\"#all\">All scans</a><a href=\"#more\">More data</a><a href=\"#news\">News &amp; filings</a><a href=\"#stock\">Stock page</a><a href=\"#tools\">More tools</a><a href=\"#road\">Roadmap</a><a href=\"#cost\">Costs</a><a href=\"#money\">Earning</a><a href=\"#pc\">Pros &amp; cons</a><a href=\"#law\">SEBI rules</a></nav>";
function legacy(h) {
  let s = String(h);
  /* Stage 1 coverage: the per-stock-file layer (a classic script with its own id) is an addition; the older pins see the page without it */
  s = s.replace(/<script type="module" id="stocklens-live">[\s\S]*?<\/script>\n/, "");
  /* the Stock Detail live strip (own module id; reads the live layer's newest answer, makes no request): an addition, so the older pins see the page without it */
  s = s.replace(/<script type="module" id="stocklens-stocklive">[\s\S]*?<\/script>\n/, "");
  s = s.replace(/<script id="stocklens-shards">[\s\S]*?<\/script>\n/, "");
  /* Quarterly Results: a read-only section module (its own id) is an addition; the older pins see the page without it */
  s = s.replace(/<script type="module" id="stocklens-quarterly">[\s\S]*?<\/script>\n/, "");
  /* All-stock dated last close (out/market_dma.json): put back the five-file loader, the historical-only price call, the plain price/Market Cap tiles and their note, so the older pins see the Stock Detail block as it was before it */
  s = s.replace('j("out/shareholding.json"),j("out/market_dma.json")]).then(function(a){return{val:a[0],fin:a[1],prices:a[2],sh:a[3],eod:a[4]}});', () => 'j("out/shareholding.json")]).then(function(a){return{val:a[0],fin:a[1],prices:a[2],sh:a[3]}});');
  s = s.replace(/\/\* Last close for EVERY NSE EQ stock[\s\S]*?(?=\/\* Market capitalisation for the header = )/, "");
  s = s.replace("var p=lastClose(d,s),has=", () => "var p=priceFor(d.prices,s),has=");
  s = s.replace('tile("Market Cap (₹ crore)",mc.cap!==null?cr(mc.cap):"Unavailable")+tile("Latest Price (last close, ₹)",p.price!==null?r2(p.price):"Unavailable")', () => 'tile("Market Cap (₹ crore)",cr(mc.cap))+tile("Latest Price (last close, ₹)",r2(p.price))');
  s = s.replace(/  h\+='<p class="note" style="padding-left:0" data-last-close=[^\n]*\n/, () => "  h+='<p class=\"note\" style=\"padding-left:0\">Latest price and day change are the last daily close in the historical data'+(p.date?\", as of \"+esc(p.date):\"\")+' (not a live price). They show \"-\" when the stock has fewer than two valid daily candles. '+(mc.cap!==null?\"Market Cap = last close × \"+esc(N0.format(mc.shares))+\" shares (NSE shareholding filing, quarter ended \"+esc(mc.quarter)+\"), not a live value.\":'Market Cap shows \"-\" unless both a last close and an NSE-filed share count are on file.')+'</p>';\n");
  /* Market Cap in the Stock Detail header (replaces the Sector tile there): put back the loader's three-file Promise.all, the Sector tile, its variables and its note */
  s = s.replace(/\/\* Market capitalisation for the header = [\s\S]*?return out\}\n\n/, "");
  s = s.replace('j("out/historical.json"),j("out/shareholding.json")]).then(function(a){return{val:a[0],fin:a[1],prices:a[2],sh:a[3]}});', () => 'j("out/historical.json")]).then(function(a){return{val:a[0],fin:a[1],prices:a[2]}});');
  s = s.replace("  var company=v.company_name||f.company_name,mc=marketCap(d.sh,s,p.price);\n", () => "  var company=v.company_name||f.company_name,sector=v.sector;\n");
  s = s.replace('tile("Market Cap (₹ crore)",cr(mc.cap))', () => 'tile("Sector",sector?esc(sector):"-")');
  s = s.replace(/ '\+\(mc\.cap!==null\?[\s\S]*?\)\+'<\/p>';/, () => "</p>';");
  /* SENSEX end-of-day card: its marked blocks (constants and functions, and the lazy file request) */
  s = s.replace(/[ \t]*\/\* SENSEX-EOD:start \*\/[\s\S]*?\/\* SENSEX-EOD:end \*\/\n/g, "");
  /* Market Today index tabs (NIFTY 50 / SENSEX): put back the single-card S_["mk-today"] head */
  s = s.replace('var INDEX_TABS=["NIFTY 50","SENSEX"];\nS_["mk-today"]=function(m,st){\n  var h=\'\',sel=INDEX_TABS.indexOf(st&&st.index)>=0?st.index:INDEX_TABS[0];\n', () => 'S_["mk-today"]=function(m){\n  var h=\'\';\n');
  s = s.replace(/  h\+='<div class="tabs mk-tabs"[\s\S]*?  if\(sel==="SENSEX"\)return h\+[^\n]*;\n/, "");
  /* Phase 5J: the market dashboard replaced the old home page (its markup, its scripts and its footer); put the old ones back */
  s = s.replace(/<style id="marketCss">[\s\S]*?<\/style>/, "");
  s = s.replace(/<script type="module" id="stocklens-market">[\s\S]*?<\/script>\n/, "");
  s = s.replace(/<!-- 5J:start -->[\s\S]*?<!-- 5J:end -->\n/, () => FX.region1);
  s = s.replace("<footer>End-of-day market data. Not investment advice.</footer>", () => FX.footer);
  s = s.replace(/<script>\nlet FD=\[\][\s\S]*?<\/script>\n/, () => FX.script0);
  s = s.replace(/#globalBar\{[\s\S]*?(?=<\/style><\/head><body>)/, "");
  s = s.replace(/<\/style><\/head><body>\n<div id="globalBar">[^\n]*\n<div class="w">/, '</style></head><body><div class="w">');
  if (!s.includes(OLDNAV)) s = s.replace('<body><div class="w">\n', '<body><div class="w">\n' + OLDNAV + '\n');   /* idempotent: a page committed before the row was removed still has it */
  s = s.split(NEWRE).join(OLDRE);
  s = s.replace(/var RENDERED=null;[^\n]*\n/, "");
  s = s.replace("if(!s){b.classList.remove(\"detail-mode\");RENDERED=null;return}\n  if(s===RENDERED&&b.classList.contains(\"detail-mode\"))return;\n  RENDERED=s;\n", "if(!s){b.classList.remove(\"detail-mode\");return}\n");
  s = s.replace(/  var gh=\$\("globalSearchHost"\),host=gh\|\|\(document\.querySelector&&document\.querySelector\("header"\)\);[^\n]*\n  if\(!host\|\|\(!gh&&!host\.parentNode\)\)return null;/, '  var host=document.querySelector&&document.querySelector("header");\n  if(!host||!host.parentNode)return null;');
  s = s.replace("'<div class=\"ss-box\">'+(gh?'':'<label for=\"companySearch\">Search stocks</label>')", "'<div class=\"ss-box\"><label for=\"companySearch\">Search stocks</label>'");
  s = s.replace("'<input id=\"companySearch\" '+(gh?'aria-label=\"Search stocks\" ':'')+'type=", "'<input id=\"companySearch\" type=");
  s = s.replace(/  if\(gh\)gh\.appendChild\(sec\);else host\.parentNode\.insertBefore\(sec,host\);[^\n]*\n/, "  host.parentNode.insertBefore(sec,host);                    /* directly under the navigation, above the page heading */\n");
  s = s.replace(/<script type="module" id="stocklens-nav">[\s\S]*?<\/script>\n/, "");
  return s;
}
module.exports = { legacy };

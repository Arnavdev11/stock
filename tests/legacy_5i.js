"use strict";
/* Phase 5I helper for the older byte-identity pins. legacy(html) reverses exactly the Phase 5I changes (the global bar markup and CSS, the
   "&section=" routing regex, the Stock Detail render guard, the search mount point and the stocklens-nav module), so an older test can
   keep asserting that its own blocks are byte-identical to what they were before Phase 5I. test_global_navigation.js proves that
   legacy(page) equals the Phase 5H.6 page (aa4ace1) exactly: nothing else changed. */
const NEWRE = '/^#stock=([^&]+)(?:&section=[A-Za-z0-9_-]{1,30})?$/', OLDRE = '/^#stock=([^&]+)$/';
function legacy(h) {
  let s = String(h);
  s = s.replace(/#globalBar\{[\s\S]*?(?=<\/style><\/head><body>)/, "");
  s = s.replace(/<\/style><\/head><body>\n<div id="globalBar">[^\n]*\n<div class="w">/, '</style></head><body><div class="w">');
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

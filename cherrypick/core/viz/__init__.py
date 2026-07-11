"""cherrypick.core.viz — a declarative dashboard-section contract + one generic renderer.

A module contributes a live card to the umbrella dashboard by emitting a small JSON payload (below); the
umbrella renders any section with no section-specific code, so a new module gets a card "for free" by
speaking this schema. This generalizes what was hand-wired for the GEX card. No broker, no network — the
umbrella subprocesses the module for the payload; this module only holds the schema, styles, and client
renderer.

Section payload (a module emits this per refresh):

    {
      "ok": true,
      "title": "GEX — SPX",            # optional; falls back to the section's configured title
      "subtitle": "exp 2026-07-10 ...",# optional line under the title
      "metrics": [                     # KPI tiles; value is a pre-formatted string (module owns units)
        {"label": "Net GEX", "value": "73.7B", "tone": "pos"},   # tone: pos|neg|accent|"" (default)
        ...
      ],
      "bars": {                        # optional compact labelled bar chart (signed, zero-centred)
        "labels": [7500, 7510, ...],   # numeric x positions (one per row)
        "focus": 7575.39,              # optional: highlight the row nearest this value (e.g. spot)
        "series": [                    # 1-2 series drawn as overlaid bars per row
          {"name": "Net GEX (OI)", "values": [...], "tone_by_sign": true},
          {"name": "Net GEX (Vol)", "values": [...], "tone": "vol"}
        ]
      },
      "note": "positioning = OI ..."   # optional footer
    }

    # or, when the module has nothing to show yet:
    {"ok": false, "error": "streamer not running"}

`tone` values map to CSS classes (pos/neg/accent/vol); anything else renders neutral.
"""

from __future__ import annotations

import html

SECTION_STYLE = (
    ".cpsection h2 .muted{font-weight:400;font-size:13px}"
    ".cpmetrics{display:flex;flex-wrap:wrap;gap:14px;margin:6px 0 12px}"
    ".cpm{min-width:96px}.cpm .k{font-size:11px;text-transform:uppercase;letter-spacing:.04em;opacity:.7}"
    ".cpm .v{font-size:17px;font-weight:650}"
    ".cprow{display:grid;grid-template-columns:64px 1fr;align-items:center;gap:8px}"
    ".cprow{margin:2px 0;font-size:12px}"
    ".cpbars{position:relative;height:16px}"
    ".cpbar{position:absolute;top:2px;height:5px;border-radius:2px}"
    ".cpbar.s1{top:9px;height:4px;opacity:.85}"
    ".cppos{background:#1a7f37}.cpneg{background:#cf222e}.cpvol{background:#9a6700}"
    ".cpaccent{color:#0969da;font-weight:650}.cprowfocus>div:first-child{color:#0969da;font-weight:650}"
    ".cperr{color:#9a6700}"
)

# Generic client renderer: finds every [data-cp-section] card, polls its data-endpoint, and renders the
# declarative payload (metrics tiles + a zero-centred, signed bar chart). Route-agnostic — the endpoint
# is read from the card's data attribute — so it never hardcodes a section id or URL.
SECTION_JS = r"""
(function(){
  function fmt(v){ if(v==null||isNaN(v)) return '0'; var a=Math.abs(v), s=v<0?'-':'';
    if(a>=1e9) return s+(a/1e9).toFixed(2)+'B'; if(a>=1e6) return s+(a/1e6).toFixed(1)+'M';
    if(a>=1e3) return s+(a/1e3).toFixed(0)+'K'; return ''+Math.round(v); }
  function tile(m){ var c=({pos:'cppos',neg:'cpneg',accent:'cpaccent',vol:'cpvol'})[m.tone]||'';
    return '<div class="cpm"><div class="k">'+m.label+'</div><div class="v '+c+'">'+m.value+'</div></div>'; }
  function toneClass(t){ return ({pos:'cppos',neg:'cpneg',accent:'cpaccent',vol:'cpvol'})[t]||''; }
  function renderBars(bars){
    var labels=bars.labels||[]; if(!labels.length) return '';
    var series=(bars.series||[]).slice(0,2), focus=bars.focus;
    // window to 21 rows around focus so near-the-focus structure stays visible
    var idx=labels.map(function(_,i){return i;});
    if(focus!=null && labels.length>21){
      var ci=0,best=1e18;
      for(var i=0;i<labels.length;i++){var dd=Math.abs(labels[i]-focus); if(dd<best){best=dd;ci=i;}}
      var lo=Math.max(0,ci-10); idx=idx.slice(lo,lo+21);
    }
    var mx=1;
    idx.forEach(function(i){ series.forEach(function(se){ mx=Math.max(mx,Math.abs(se.values[i]||0)); }); });
    var near=null;
    if(focus!=null){ var b2=1e18;
      idx.forEach(function(i){var d=Math.abs(labels[i]-focus); if(d<b2){b2=d;near=i;}}); }
    return idx.map(function(i){
      var bars_html = series.map(function(se,si){
        var v=se.values[i]||0, w=Math.min(50,Math.abs(v)/mx*50), left=v>=0?50:(50-w);
        var cls=se.tone_by_sign?(v>=0?'cppos':'cpneg'):(toneClass(se.tone)||'cpaccent');
        return '<div class="cpbar '+(si===1?'s1 ':'')+cls+'" style="left:'+left+'%;width:'+w+'%"></div>';
      }).join('');
      return '<div class="cprow'+(i===near?' cprowfocus':'')+'"><div>'+labels[i]+'</div>'
           + '<div class="cpbars">'+bars_html+'</div></div>';
    }).join('');
  }
  function render(card, d){
    var sub=card.querySelector('.cpsub'), met=card.querySelector('.cpmetrics');
    var ch=card.querySelector('.cpchart'), note=card.querySelector('.cpnote');
    if(!d||!d.ok){ sub.className='cpsub cperr'; sub.textContent=(d&&d.error)?d.error:'no data';
      met.innerHTML=''; ch.innerHTML=''; if(note) note.textContent=''; return; }
    sub.className='cpsub muted'; sub.textContent=d.subtitle||'';
    if(d.title){ card.querySelector('h2').childNodes[0].nodeValue=d.title+' '; }
    met.innerHTML=(d.metrics||[]).map(tile).join('');
    ch.innerHTML=d.bars?renderBars(d.bars):'';
    if(note) note.textContent=d.note||'';
  }
  function wire(card){
    var url=card.getAttribute('data-endpoint'), refresh=(+card.getAttribute('data-refresh')||15)*1000;
    function tick(){ fetch(url).then(function(r){return r.json();})
      .then(function(d){render(card,d);}).catch(function(){}); }
    tick(); setInterval(tick, refresh);
  }
  document.querySelectorAll('[data-cp-section]').forEach(wire);
})();
"""


def card_skeleton_html(section_id: str, title: str, endpoint: str, refresh: int = 15) -> str:
    """The static card skeleton the umbrella injects per enabled section; `SECTION_JS` fills it live.

    `endpoint` is the URL the card polls (the umbrella owns the route naming); the renderer reads it
    from the data attribute, so this module stays route-agnostic.
    """
    sid = html.escape(section_id)
    return (
        f'<section class="card cpsection" data-cp-section="{sid}" '
        f'data-endpoint="{html.escape(endpoint)}" data-refresh="{int(refresh)}">'
        f"<h2>{html.escape(title)} <span class=\"cpsub muted\">loading…</span></h2>"
        '<div class="cpmetrics"></div><div class="cpchart"></div>'
        '<div class="meta"><span class="cpnote muted"></span></div></section>'
    )

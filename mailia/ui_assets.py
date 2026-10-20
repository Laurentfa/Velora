"""Feuille de style et scripts de la fenêtre (comportements côté navigateur : colonnes, éditeur, glisser-déposer, raccourcis…)."""



CSS = """
:root{--bg:#f5f7fa;--side:#e9eef5;--surface:#fff;--surface2:#eef2f8;--text:#1b2430;--muted:#66768a;--line:#dfe6ef;
 --accent:#0a66e8;--accent-soft:#e1edff;--ai:#0a66e8;--ai-soft:#eaf3ff;--hi:#d92d3a;--hi-soft:#fdecee;--sel:#d6e6ff}
.q-tooltip{background:var(--surface)!important;color:var(--text)!important;border:1px solid var(--line);border-radius:8px;box-shadow:0 6px 20px rgba(20,40,80,.18);font-size:12.5px;padding:6px 10px;pointer-events:none;animation:mtip 4.2s forwards}
@keyframes mtip{0%,47%{opacity:0}52%,95%{opacity:1}100%{opacity:0;visibility:hidden}}
.csvt{border-collapse:collapse;font-size:13px;white-space:nowrap}.csvt th,.csvt td{border:1px solid var(--line);padding:5px 10px;text-align:left;max-width:360px;overflow:hidden;text-overflow:ellipsis;user-select:text}
.csvt th{position:sticky;top:0;background:var(--surface);font-weight:700;z-index:1}.csvt tbody tr:nth-child(even){background:var(--surface)}
.srcrow{display:flex;align-items:center;gap:12px;width:100%;padding:8px 12px;border:1px solid var(--line);border-radius:10px;background:var(--surface)}.srcrow:hover{background:var(--accent-soft)}
.srcnum{flex:none;min-width:26px;height:26px;border-radius:13px;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;background:var(--surface2);color:var(--muted)}.srcnum.cited{background:var(--accent);color:#fff}
input::-webkit-caps-lock-indicator{visibility:hidden!important;display:none!important;opacity:0!important}
.sidetop{display:flex;flex-direction:column;flex:none;box-sizing:border-box;height:104px;margin:0 -10px;padding:0 10px;border-bottom:1px solid var(--line);width:calc(100% + 20px)}
.sidescroll{flex:1 1 auto;min-height:0;width:calc(100% + 10px);margin-right:-10px;padding-top:8px}
.sidescroll .q-scrollarea__content{width:100%;max-width:100%;padding-right:10px}
body.body--dark{--bg:#13171e;--side:#171c25;--surface:#1a2029;--surface2:#232b37;--text:#e8edf4;--muted:#8a98ab;--line:#29323f;
 --accent:#4a9bff;--accent-soft:#183054;--ai:#6cb0ff;--ai-soft:#172a45;--hi:#ff6b75;--hi-soft:#3b1f25;--sel:#1d3659}
html,body{overflow:hidden;background:var(--bg);color:var(--text)}
/* fenêtres, champs, arbre, cases, menus : mêmes couleurs que le reste de l'appli, en clair comme en sombre */
.q-dialog__backdrop{background:rgba(10,20,40,.45)!important;backdrop-filter:blur(3px)}
.q-dialog .q-card{background:var(--surface)!important;color:var(--text)!important;border:1px solid var(--line);border-radius:18px;box-shadow:0 24px 70px rgba(10,30,70,.25)}
.q-menu{background:var(--surface)!important;color:var(--text)!important;border:1px solid var(--line);border-radius:12px;box-shadow:0 12px 36px rgba(10,30,70,.2)}
.q-item--active,.q-item.q-router-link--active{color:var(--accent)!important}
/* boutons : coins arrondis, relief léger, survol et appui vivants (la barre d'outils de l'éditeur garde son aspect) */
.q-btn:not(.q-btn--round):not(.q-editor .q-btn){border-radius:11px;transition:box-shadow .18s,transform .12s,background-color .18s,filter .18s;letter-spacing:.01em}
.q-btn.q-btn--dense:not(.q-btn--round):not(.q-editor .q-btn){border-radius:9px}
.q-btn--unelevated.bg-primary,.q-btn--unelevated.bg-negative,.q-btn--unelevated.bg-positive{
 box-shadow:0 1px 2px rgba(10,50,120,.28),0 4px 10px -4px rgba(10,90,220,.45),inset 0 1px 0 rgba(255,255,255,.22);font-weight:600}
.q-btn--unelevated.bg-primary{background:linear-gradient(180deg,color-mix(in srgb,var(--accent) 84%,#fff),var(--accent))!important;color:#fff!important}
body.body--dark .q-btn--unelevated.bg-primary{color:#06182f!important}
.q-btn--unelevated.bg-negative{background:linear-gradient(180deg,#f0545f,#d92d3a)!important;box-shadow:0 1px 2px rgba(120,10,20,.3),0 4px 10px -4px rgba(217,45,58,.5),inset 0 1px 0 rgba(255,255,255,.22)}
.q-btn--unelevated.bg-primary:hover,.q-btn--unelevated.bg-negative:hover{filter:brightness(1.07);transform:translateY(-1px);
 box-shadow:0 2px 4px rgba(10,50,120,.3),0 8px 16px -6px rgba(10,90,220,.55),inset 0 1px 0 rgba(255,255,255,.25)}
.q-btn--unelevated.bg-negative:hover{box-shadow:0 2px 4px rgba(120,10,20,.3),0 8px 16px -6px rgba(217,45,58,.55),inset 0 1px 0 rgba(255,255,255,.25)}
.q-btn:not(.q-btn--round):not(.q-editor .q-btn):active{transform:translateY(0) scale(.98);filter:brightness(.97)}
.q-btn--outline:not(.q-btn--round):not(.q-editor .q-btn){background:var(--surface);box-shadow:0 1px 2px rgba(20,40,80,.08)}
.q-btn--outline:not(.q-btn--round):not(.q-editor .q-btn):hover{background:var(--accent-soft);box-shadow:0 3px 8px -3px rgba(10,90,220,.3);transform:translateY(-1px)}
.q-btn--outline:before{border-width:1.5px!important;border-radius:inherit}
.q-btn--flat:not(.q-btn--round):not(.q-editor .q-btn):not(.nav):hover{background:var(--surface2)}
.q-btn--flat.text-primary:not(.q-btn--round):hover{background:var(--accent-soft)}
.q-btn--round:not(.q-editor .q-btn){transition:background-color .18s,transform .12s}
.q-btn--round:not(.q-editor .q-btn):hover{background:var(--surface2)}
.q-btn--round:not(.q-editor .q-btn):active{transform:scale(.92)}
.q-btn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.q-btn .q-focus-helper{border-radius:inherit}
.q-field--outlined .q-field__control:before{border-color:var(--line)!important}
.q-field--outlined:hover .q-field__control:before{border-color:var(--muted)!important}
.q-field--outlined.q-field--focused .q-field__control:after{border-color:var(--accent)!important}
.q-field__label,.q-field__marginal{color:var(--muted)!important}
.q-field--focused .q-field__label{color:var(--accent)!important}
.q-field__native,.q-field__input,.q-field__prefix,.q-field__suffix{color:var(--text)!important}
.q-field__native::placeholder,.q-field__input::placeholder{color:var(--muted)!important;opacity:.8}
.q-tree__node-header{border-radius:9px;padding:3px 8px;margin:1px 0}
.q-tree__node-header:hover{background:var(--surface2)}
.q-tree__arrow{color:var(--muted)}
.q-tree__node:after,.q-tree__node--parent>.q-tree__node-header:before{border-color:var(--line)!important}
.q-checkbox__inner{color:var(--muted)!important}
.q-checkbox__inner--truthy,.q-checkbox__inner--indet{color:var(--accent)!important}
.q-checkbox__label,.q-toggle__label{color:var(--text)}
.q-toggle__inner--truthy{color:var(--accent)!important}
.q-scrollarea__thumb{background:var(--muted);opacity:.35;border-radius:6px}
.treehint{font-size:12px;color:var(--muted)}
.nicegui-content{padding:0!important;gap:0!important;height:100vh}
.q-page{padding:0!important}
.root{position:relative;display:grid;grid-template-columns:var(--w-side,240px) var(--w-list,400px) minmax(0,1fr);width:100%;height:100vh;gap:0}
.resizer{position:absolute;top:0;bottom:0;width:11px;margin-left:-5px;cursor:col-resize;z-index:30;touch-action:none}
.resizer::after{content:"";position:absolute;left:5px;top:0;bottom:0;width:1px;background:transparent;transition:background .15s,width .15s,left .15s}
.resizer:hover::after,.resizer.drag::after{background:rgba(128,128,128,.55);width:3px;left:4px}  /* gris neutre (clair et sombre), plus discret que le bleu */
.resizer.r1{left:var(--w-side,240px)}
.resizer.r2{left:calc(var(--w-side,240px) + var(--w-list,400px))}
body.resizing{cursor:col-resize!important;user-select:none!important}
body.resizing iframe{pointer-events:none}
.side{background:var(--side);border-right:1px solid var(--line);padding:16px 10px;overflow:hidden;height:100vh;flex-wrap:nowrap}
.brand{display:flex;align-items:center;gap:13px;font-weight:700;font-size:26px;letter-spacing:-.01em;padding:2px 10px 14px}.brand img{border-radius:10px}
.nav{display:flex;align-items:center;gap:10px;width:100%;padding:8px 10px;border-radius:9px;cursor:pointer;user-select:none}
.nav:hover{background:var(--surface2)}.nav.on{background:var(--sel);font-weight:600}
.nav .ic{width:21px;text-align:center;color:var(--accent)}
.nav .lbl{flex:1}.nav .cnt{font-size:12px;color:var(--muted)}
.nav .cnt.hot{background:var(--accent);color:#fff;border-radius:999px;padding:0 7px;font-weight:600}
.nav.sub{padding:6px 10px;font-size:14px}
.nav.ghost{color:var(--muted)}.nav.ghost .lbl{opacity:.8}
.grp{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);padding:16px 10px 4px}
.listcol{background:var(--surface);border-right:1px solid var(--line);height:100vh;min-width:0;flex-wrap:nowrap}
.lhead{box-sizing:border-box;height:120px;padding:14px 14px 0;border-bottom:1px solid var(--line);width:100%}
.lhead .title{font-size:19px;font-weight:700;letter-spacing:-.02em}
.ephem{display:flex;align-items:center;flex-wrap:wrap;font-size:13px;color:var(--muted);margin-top:1px}.eph-i{display:inline-flex;align-items:center;gap:3px}.eph-i img{width:24px;height:24px;margin:-3px 0}.ephem .sep{margin:0 8px}
.agenda{width:100%;height:100vh;background:transparent;flex-wrap:nowrap}.ag-head{padding:14px 18px 8px;width:100%}.ag-title{font-size:19px;font-weight:700;letter-spacing:-.01em}
.ag-scroll{flex:1;min-height:0;width:100%;overflow:auto;padding:0 18px 12px;scrollbar-width:none}.ag-scroll::-webkit-scrollbar{display:none}.ag-note{padding:4px 18px 10px;color:var(--danger,#d92d3a)}
.ag-days,.ag-allday,.ag-grid{display:grid;width:100%}.ag-days{position:sticky;top:0;z-index:3;background:transparent;backdrop-filter:blur(6px)}.ag-dh{text-align:center;padding:6px 0;font-size:13px;color:var(--muted)}.ag-dh span{text-transform:capitalize}
.ag-today{color:var(--accent);font-weight:700}.ag-adc{border-left:1px solid var(--line);padding:2px;min-height:4px}.ag-ad{font-size:11px;border-radius:5px;padding:1px 6px;margin-bottom:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ag-hrs{position:relative}.ag-hr{font-size:10px;color:var(--muted);position:relative}.ag-hr span{position:absolute;top:-6px;right:6px}
.ag-col{position:relative;border-left:1px solid var(--line);background-image:linear-gradient(var(--line) 1px,transparent 1px);background-size:100% 40px;cursor:pointer}.ag-col:hover{background-color:var(--hover)}
.ag-ev{position:absolute;border-radius:6px;padding:2px 6px;font-size:11px;line-height:1.25;overflow:hidden;border-left:3px solid;cursor:default;box-sizing:border-box}.ag-ev b{font-weight:600}
.ag-ex{background:rgba(54,121,255,.14);border-color:#3679ff;color:var(--text)}.ag-go{background:rgba(30,160,90,.15);border-color:#1ea05a;color:var(--text)}.ag-both{background:rgba(140,90,220,.15);border-color:#8c5adc;color:var(--text)}
.ag-now{position:absolute;left:0;right:0;border-top:2px solid #d92d3a;pointer-events:none;z-index:2}.ag-empty{padding:40px 10px;text-align:center;color:var(--muted)}
.lhead .subtitle{font-size:13px;color:var(--muted);margin-top:2px}
.rows{flex:1;width:100%;min-height:0}
.rows .q-scrollarea__content,.rbody .q-scrollarea__content{width:100%;max-width:100%}
.listcol,.readercol{overflow:hidden}
.mrow{max-width:100%;box-sizing:border-box}
.mrow{display:flex;gap:10px;width:100%;padding:11px 14px 11px 10px;border-bottom:1px solid var(--line);cursor:pointer}
.mrow:hover{background:var(--surface2)}.mrow.sel{background:var(--sel)}
.mrow .dot{width:8px;height:8px;border-radius:50%;margin-top:7px;flex:none;background:var(--accent)}
.mrow.read .dot{background:transparent}
.mrow .rb{min-width:0;flex:1}
.mrow .top{display:flex;justify-content:space-between;gap:8px;align-items:baseline}
.mrow .from{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mrow.read .from{font-weight:500}
.mrow .date{font-size:12px;color:var(--muted);white-space:nowrap}
.mrow .subj{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mrow.read .subj{color:var(--muted)}
.mrow .sum{color:var(--muted);font-size:13px;margin-top:2px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.mrow .sum::before{content:"✦ ";color:var(--ai)}
.mrow .sum.snip::before{content:none}
/* liste compacte : une ligne par mail (expéditeur, date, objet) ; le résumé et les pastilles disparaissent, les actions apparaissent au survol */
body.compact .mrow{padding:5px 12px 5px 10px;gap:8px}
body.compact .mrow .dot{margin-top:5px}
body.compact .mrow .sum:not(.snip){display:none}
body.compact .badges{margin-top:0}
body.compact .badges .badge:not(.p3){display:none}
body.compact .badges:not(:has(.p3)){display:block;margin:0}
body.compact .mrow .rowact{position:absolute;right:8px;bottom:2px;opacity:0;background:var(--surface2);border-radius:8px}
body.compact .swrap:hover .rowact{opacity:1}
.mrow mark{background:#ffe566;color:#1d1d1f;border-radius:3px;padding:0 1px}
.badges{display:flex;gap:5px;margin-top:5px;flex-wrap:wrap}
.badge{font-size:11px;font-weight:600;padding:1px 7px;border-radius:999px;background:var(--surface2);color:var(--muted)}
.badge.p3{background:var(--hi-soft);color:var(--hi)}.badge.rep{background:var(--accent-soft);color:var(--accent)}
.badge.acc{background:transparent;border:1px solid var(--line)}
.rowact{margin-left:auto;display:flex;gap:0;align-items:center;opacity:.45;transition:opacity .12s}
.swrap:hover .rowact,.sel .rowact{opacity:1}
.rowact .q-btn{color:var(--muted);padding:2px}.rowact .q-btn:hover{color:var(--accent);background:var(--accent-soft)}
.rowact .q-btn:last-child:hover{color:var(--hi);background:var(--hi-soft)}
.sect{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);padding:14px 14px 6px;display:flex;justify-content:space-between;width:100%}
.aicard{background:var(--ai-soft);border-radius:12px;padding:12px 14px;margin:12px 12px 4px;width:calc(100% - 24px)}
.aicard .tag{font-size:11px;font-weight:700;color:var(--ai);letter-spacing:.06em;text-transform:uppercase}
.aicard .sub{color:var(--muted);font-size:12px}
.empty{text-align:center;color:var(--muted);padding:40px 16px;width:100%}
.readercol{background:var(--bg);height:100vh;min-width:0;flex-wrap:nowrap}
.rhead{padding:14px 22px;border-bottom:1px solid var(--line);background:var(--surface);width:100%}
.rhead h2{margin:0;font-size:19px;font-weight:700;line-height:1.3;letter-spacing:-.01em}
.rmeta{font-size:13px;color:var(--muted)}.rmeta b{color:var(--text)}
.rbody{flex:1;width:100%;min-height:0}
.rcontent{padding:16px 22px 40px;width:100%}
.rcontent .aicard{margin:0 0 14px;width:100%}
.ctxm{position:fixed;z-index:10000;min-width:200px;padding:6px;background:var(--surface);color:var(--text);border:1px solid var(--line);border-radius:12px;box-shadow:0 12px 36px rgba(10,30,70,.25);font-size:14px}
.ctxit{padding:7px 12px;border-radius:8px;cursor:pointer;user-select:none}.ctxit:hover{background:var(--accent-soft);color:var(--accent)}
.ctxsep{height:1px;background:var(--line);margin:5px 4px}
.mailbody{white-space:pre-wrap;word-break:break-word;background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:16px;line-height:1.55;width:100%}
.composer{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:14px;width:100%;margin-bottom:14px}
.composer.dropping{outline:2px dashed var(--accent);outline-offset:-7px;background:var(--accent-soft)}
.placeholder{margin:auto;text-align:center;color:var(--muted)}.placeholder .big{font-size:40px;color:var(--ai)}
.mrow .right{display:flex;gap:8px;align-items:center;flex:none}
.mrow.picked{background:var(--accent-soft)!important;box-shadow:inset 3px 0 0 var(--accent)}
.bulkbar{position:sticky;top:0;z-index:6;padding:8px 12px;background:var(--surface);border-bottom:1px solid var(--line);box-shadow:0 2px 6px rgba(10,30,70,.08)}
.convn{font-size:11px;font-weight:700;min-width:20px;text-align:center;padding:0 6px;border-radius:999px;background:var(--accent-soft);color:var(--accent)}
.tcard{background:var(--surface);border:1px solid var(--line);border-radius:12px;width:100%;overflow:hidden}
.tcard.focus{border-color:var(--accent)}
.thead{display:flex;gap:10px;padding:10px 14px;cursor:pointer;align-items:flex-start;flex-wrap:nowrap}
.thead:hover{background:var(--surface2)}
.thead .who{min-width:0;flex:1}
.thead .name{font-weight:600}.thead .name.unread::before{content:"";display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--accent);margin-right:6px}
.thead .snip{color:var(--muted);font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.thead .when{font-size:12px;color:var(--muted);white-space:nowrap;flex:none}
.tbody{padding:0 14px 14px;width:100%}
.tbody .mailbody{border:0;padding:8px 0 0;background:transparent}
.me-badge{display:inline-block;font-size:11px;font-weight:600;color:var(--accent);background:var(--accent-soft);border-radius:999px;padding:0 7px;margin-left:6px}
.swrap{position:relative;overflow:hidden;width:100%;border-bottom:1px solid var(--line);touch-action:pan-y;user-select:none;-webkit-user-select:none}
.swrap .mrow{position:relative;z-index:1;background:var(--surface);border-bottom:0;will-change:transform}
.swrap .mrow:hover{background:var(--surface2)}.swrap .mrow.sel{background:var(--sel)}
.mailframe{width:100%;height:80px;border:1px solid var(--line);border-radius:10px;background:#fff;color-scheme:light;display:block}
.mailnote{width:100%;background:var(--accent-soft);color:var(--text);border-radius:10px;padding:6px 12px;font-size:13px}
.brief{background:var(--ai-soft);border-radius:16px;padding:16px 18px 14px;margin:12px 12px 6px;width:calc(100% - 24px)}
.brief-head{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.brief .tag{font-size:11px;font-weight:700;color:var(--ai);letter-spacing:.06em;text-transform:uppercase}
.wx{border-radius:14px;padding:12px 14px 8px;margin:14px 0 22px;background:linear-gradient(135deg,rgba(60,110,200,.26),rgba(60,110,200,.10));border:1px solid var(--line)}
.wx img{filter:drop-shadow(0 1px 2px rgba(40,60,110,.35))}
.wx-main{display:flex;align-items:center;gap:6px}
.wx-icon{width:92px;height:92px;flex:none;margin:-6px 0 -6px -8px}
.wx-temp{font-size:44px;font-weight:700;letter-spacing:-.03em;line-height:1;margin-right:10px}
.wx-info{min-width:0}.wx-label{font-weight:600;font-size:15px}.wx-place{font-size:12px;color:var(--muted);margin-bottom:3px}
.wx-sub{font-size:12px;color:var(--muted);display:flex;align-items:center;gap:3px;flex-wrap:wrap}.wx-sub img{width:16px;height:16px;margin-left:4px}.wx-sub img:first-child{margin-left:0}
.wx-hours{display:flex;gap:2px;margin-top:6px;border-top:1px solid var(--line);padding-top:6px;overflow-x:auto;scrollbar-width:none}.wx-hours::-webkit-scrollbar,.wx-days::-webkit-scrollbar{display:none}
.wx-h{display:flex;flex-direction:column;align-items:center;min-width:52px;flex:1;font-size:12px}.wx-h span{color:var(--muted)}.wx-h img{width:34px;height:34px}.wx-h b{font-size:13px}
.wx-h i{font-style:normal;color:var(--muted);font-size:10px;display:flex;align-items:center}.wx-h i img{width:11px;height:11px}
.wx-days{display:flex;gap:2px;margin-top:6px;border-top:1px solid var(--line);padding-top:6px;overflow-x:auto;scrollbar-width:none}
.wx-d{display:flex;flex-direction:column;align-items:center;min-width:64px;flex:1;font-size:12px;border-radius:10px;padding:2px 0}.wx-d:first-child{background:rgba(255,255,255,.18)}
.wx-d span{color:var(--muted);white-space:nowrap}.wx-d img{width:36px;height:36px}.wx-d b{font-size:13px}.wx-d em{font-style:normal;color:var(--muted);font-size:12px}
.wx-d i{font-style:normal;color:var(--muted);font-size:10px;display:flex;align-items:center}.wx-d i img{width:11px;height:11px}
.wx-det{margin-top:8px;border-top:1px solid var(--line);padding-top:4px;font-size:12px}.wx-det summary{cursor:pointer;color:var(--muted);padding:2px 0;user-select:none}
.wx-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:6px 12px;padding:6px 0 2px}.wx-grid span{display:block;color:var(--muted);font-size:11px}.wx-grid b{font-size:13px}
.wx-credit{font-size:10px;color:var(--muted);text-align:right;margin-top:2px;opacity:.8}
.brief-date{font-size:12px;color:var(--muted)}
.brief-intro{font-size:16px;font-weight:600;line-height:1.4;margin:10px 0 2px;letter-spacing:-.01em}
.brief-sec{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);margin:16px 0 4px}
.bitem{display:flex;gap:12px;padding:9px 8px;margin:0 -8px;border-radius:10px;cursor:pointer;align-items:flex-start}
.bitem:hover{background:var(--surface)}
.bdot{width:8px;height:8px;border-radius:50%;background:var(--accent);margin-top:7px;flex:none}
.bdot.hi{background:var(--hi)}
.btext{min-width:0;font-size:14px;line-height:1.45}
.bmeta{font-size:12px;color:var(--muted);margin-top:1px}
.brief-info{font-size:13px;color:var(--muted);margin-top:14px;padding-top:12px;border-top:1px solid var(--line);line-height:1.5}
.pvframe{width:100%;height:100%;border:0;min-height:600px;background:#fff;color-scheme:light}
body.body--dark .pvframe{filter:invert(1) hue-rotate(180deg);background:#dae0e9}  /* comme les mails : le document (build_doc) prévoit cette inversion en thème sombre */
.mailedit{border:1px solid var(--line);border-radius:12px;overflow:hidden;flex-shrink:0}
.mailedit .q-editor__toolbars-container{background:var(--surface2)}
.mailedit .q-editor__toolbar{border-bottom:1px solid var(--line);flex-wrap:wrap}
.mailedit .q-editor__content{background:#fff;color:#1b2430;font-family:Calibri,Arial,sans-serif;font-size:11pt;line-height:1.45}
body.body--dark .mailframe,body.body--dark .mailedit .q-editor__content{filter:invert(1) hue-rotate(180deg)}
body.body--dark .mailedit .q-editor__content img{filter:invert(1) hue-rotate(180deg)}
body.body--dark .mailedit .q-editor__content{background:#dae0e9}  /* devient #1a2029 (le fond sombre de l'appli) après inversion */
body.body--dark .mailframe{background:#dae0e9}
.mailedit .q-editor__content blockquote{margin:0 0 0 4px;padding-left:12px;border-left:3px solid #d6e0ee;color:#44546a}
.mailedit .q-editor__content table td{border:1px solid #c5d0e0}
.mailedit .q-editor__content img{max-width:100%;height:auto}
.mailedit .q-btn{color:var(--text)}
.swrap.dragging{opacity:.45}
body.dragging-mail .droptarget:not(.valid){opacity:.38}
.navfolder.dragging-f{opacity:.4}
.navfolder.ins-before{box-shadow:0 -2px 0 0 var(--accent)}.navfolder.ins-after{box-shadow:0 2px 0 0 var(--accent)}
.droptarget.valid{outline:1.5px dashed var(--accent);outline-offset:-2px}
.droptarget.over{background:var(--sel)!important;outline-style:solid}
.droppanel{display:none;position:absolute;left:10px;right:10px;bottom:62px;z-index:5;background:var(--surface);border:1px solid var(--line);
 border-radius:14px;padding:8px;box-shadow:0 14px 40px rgba(10,30,70,.22)}
body.dragging-mail .droppanel{display:block}
.dp-title{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);padding:2px 8px 6px}
.dp-item{display:flex;align-items:center;gap:10px;padding:8px 10px;border-radius:10px;font-size:14px}
.side{position:relative}
.sidehot{display:none}
.root{transition:grid-template-columns .16s ease}
body.resizing .root{transition:none}
html.s-hide .root{grid-template-columns:0 var(--w-list,400px) minmax(0,1fr)}
html.s-hide:not(.s-peek) .side{overflow:hidden;padding-left:0;padding-right:0;border-right:0;visibility:hidden}
html.s-hide.s-peek .root{grid-template-columns:var(--w-side,240px) var(--w-list,400px) minmax(0,1fr)}
html.s-hide:not(.s-peek) .resizer.r1{display:none}
html.s-hide:not(.s-peek) .resizer.r2{left:var(--w-list,400px)}
html.s-hide .sidehot{display:block;position:fixed;left:0;top:0;bottom:0;width:8px;z-index:70}
html:not(.s-hide) .sidepinbtn,html.s-hide:not(.s-peek) .sidepinbtn{display:none}
html.s-hide .sidehidebtn{display:none}
.q-editor__content table{max-width:100%}
/* écran peu haut : la liste des onglets défile comme le reste (molette, pavé tactile, barre fine) au lieu des petites flèches de Quasar */
.settings-tabs .q-tabs__arrow{display:none!important}
.settings-tabs .q-tabs__content{overflow-y:auto!important;min-height:0;scrollbar-width:thin;overscroll-behavior:contain}
.settings-tabs .q-tab{justify-content:flex-start;min-height:48px;padding:0 18px}.settings-tabs .q-tab__label{font-size:14px}
.mimg-box{position:fixed;display:none;z-index:6900;border:2px solid var(--accent);pointer-events:none;box-sizing:border-box}
.mimg-h{position:absolute;right:-7px;bottom:-7px;width:12px;height:12px;background:var(--accent);border:2px solid #fff;border-radius:3px;cursor:nwse-resize;pointer-events:auto}
.mimg-drop{position:fixed;display:none;z-index:6950;width:2px;background:var(--accent);pointer-events:none}
.mimg-bar .hint{color:var(--muted);font-size:11.5px}
.q-editor__content img{cursor:pointer;display:inline;vertical-align:baseline}
.phishwarn{width:100%;padding:12px 14px;border-radius:12px;background:#fff4e5;color:#7a4300;border:1px solid #f1c27a}
.phishwarn.high{background:#fdecea;color:#8a1c14;border-color:#f3a8a1}
body.body--dark .phishwarn{background:#3a2a10;color:#f6d7a2;border-color:#7a5a20}body.body--dark .phishwarn.high{background:#3d1815;color:#f6b9b3;border-color:#8a3b34}
.undobar{position:fixed;left:50%;bottom:26px;transform:translateX(-50%);z-index:8000;display:flex;align-items:center;gap:14px;padding:8px 10px 8px 18px;background:#1b2430;color:#fff;border-radius:12px;box-shadow:0 8px 28px rgba(0,0,0,.35);font-size:14px}
.tblpick{position:fixed;z-index:7100;padding:10px;background:var(--surface);color:var(--text);border:1px solid var(--line);border-radius:12px;box-shadow:0 8px 28px rgba(20,40,80,.25);font-size:12.5px}
.tblpick .pg{display:grid;gap:3px}.tblpick .pg div{width:22px;height:20px;border:1px solid var(--line);border-radius:3px;background:var(--surface2);cursor:pointer}
.tblpick .pg div.on{background:var(--sel);border-color:var(--accent)}
.tblpick .pl{margin:8px 2px;color:var(--muted);font-weight:600}
.tblpick .pr{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.tblpick .pr label{display:flex;align-items:center;gap:5px;color:var(--muted)}
.tblpick input{width:52px;font:inherit;color:var(--text);background:var(--surface2);border:1px solid var(--line);border-radius:7px;padding:2px 5px}
.tblpick button{font:inherit;cursor:pointer;color:#fff;background:var(--accent);border:0;border-radius:8px;padding:4px 12px}
.tblbar{position:fixed;display:none;z-index:7000;align-items:center;gap:10px;flex-wrap:wrap;max-width:min(920px,96vw);padding:6px 10px;background:var(--surface);color:var(--text);border:1px solid var(--line);border-radius:12px;box-shadow:0 6px 24px rgba(20,40,80,.22);font-size:12.5px}
.tblbar .tt{font-weight:700;color:var(--accent)}
.tblbar label{display:flex;align-items:center;gap:5px;color:var(--muted)}
.tblbar select,.tblbar input[type=color]{font:inherit;color:var(--text);background:var(--surface2);border:1px solid var(--line);border-radius:7px;padding:2px 4px}
.tblbar input[type=color]{width:30px;height:24px;padding:1px}
.tblbar button{font:inherit;cursor:pointer;color:var(--accent);background:transparent;border:1px solid var(--line);border-radius:8px;padding:3px 9px}
.tblbar button:hover{background:var(--surface2)}.tblbar button.danger{color:#c62828}
.sidehead{display:flex;align-items:center;justify-content:space-between;flex-wrap:nowrap;width:100%}
.swl,.swr{position:absolute;top:0;bottom:0;display:flex;align-items:center;opacity:0;transition:opacity .12s}
.swl{left:0;width:150px;background:var(--accent);color:#fff;padding-left:20px;gap:8px;font-weight:600;font-size:13px}
.swr{right:0}
.swr .swb{width:76px;height:100%;border-radius:0;font-size:12px}
@media (max-width:1023px){
  .root{grid-template-columns:1fr}
  .resizer{display:none}
  .side{display:none}
  .root.reading .listcol{display:none}
  .root:not(.reading) .readercol{display:none}
  .rhead{padding:12px 14px}.rcontent{padding:14px 14px 80px}
  .rows{padding-bottom:64px}
}
"""


SWIPE_JS = """<script>
(() => {
  if (window.__swipeReady) return; window.__swipeReady = true;
  const OPEN = -152, MAXL = -200, MAXR = 130, READ_AT = 85, ARCHIVE_AT = -185, SHOW_AT = -55;
  let st = null, openRow = null, suppress = false, wx = 0, wt = null, ww = null;
  const fg = w => w.querySelector('.mrow');
  function setX(w, x, anim) {
    fg(w).style.transition = anim ? 'transform .18s ease' : 'none';
    fg(w).style.transform = 'translateX(' + x + 'px)';
    w.querySelector('.swl').style.opacity = x > 0 ? Math.min(1, x / READ_AT) : 0;
    w.querySelector('.swr').style.opacity = x < 0 ? Math.min(1, -x / 60) : 0;
    w.dataset.x = x;
  }
  function closeOpen(except) { if (openRow && openRow !== except) { setX(openRow, 0, true); openRow = null; } }
  function emit(w, action) { w.dispatchEvent(new CustomEvent('swipe', {detail: {action}})); }
  function settle(w) {
    const x = parseFloat(w.dataset.x || 0);
    if (x > READ_AT) { setX(w, 0, true); emit(w, 'read'); }
    else if (x < ARCHIVE_AT) { setX(w, 0, true); emit(w, 'archive'); }
    else if (x < SHOW_AT) { setX(w, OPEN, true); openRow = w; }
    else setX(w, 0, true);
  }
  document.addEventListener('pointerdown', e => {
    const w = e.target.closest('.swrap');
    if (!w) { closeOpen(null); return; }
    if (e.target.closest('.swr')) return;
    closeOpen(w);
    if (e.pointerType === 'mouse') return;  // à la souris, glisser = déplacer vers un dossier (glisser-déposer)
    st = {w, x: e.clientX, y: e.clientY, base: parseFloat(w.dataset.x || 0), active: false, id: e.pointerId};
  }, true);
  document.addEventListener('pointermove', e => {
    if (!st) return;
    const dx = e.clientX - st.x, dy = e.clientY - st.y;
    if (!st.active) {
      if (Math.abs(dx) > 10 && Math.abs(dx) > Math.abs(dy) * 1.5) { st.active = true; try { st.w.setPointerCapture(st.id); } catch (_) {} }
      else if (Math.abs(dy) > 10) { st = null; return; }
      else return;
    }
    setX(st.w, Math.max(MAXL, Math.min(MAXR, st.base + dx)), false);
  }, true);
  function finish() {
    if (!st) return;
    const w = st.w, active = st.active; st = null;
    if (!active) return;
    suppress = true; setTimeout(() => suppress = false, 60);
    settle(w);
  }
  document.addEventListener('pointerup', finish, true);
  document.addEventListener('pointercancel', finish, true);
  document.addEventListener('click', e => { if (suppress) { e.stopPropagation(); e.preventDefault(); } }, true);
  // éditeur de message : mémoire du curseur et insertion de HTML à cet endroit
  let edRange = null;
  document.addEventListener('selectionchange', () => {
    const s = getSelection();
    if (s.rangeCount && s.anchorNode) {
      const el = s.anchorNode.nodeType === 1 ? s.anchorNode : s.anchorNode.parentElement;
      if (el && el.closest && el.closest('.q-editor__content')) edRange = s.getRangeAt(0).cloneRange();
    }
  });
  // listes « Police » et « Taille » : la ligne qui correspond au texte sous le curseur est mise en évidence à l'ouverture
  const marks = menu => {
    const n = edRange && (edRange.startContainer.nodeType === 1 ? edRange.startContainer : edRange.startContainer.parentElement);
    if (!n || !n.closest || !n.closest('.q-editor__content')) return;
    const items = [...menu.querySelectorAll('.q-item')];
    const label = i => (i.innerText || '').trim().toLowerCase();
    const cs = getComputedStyle(n);
    let want = null;
    const isDef = i => label(i) === 'police par défaut' || label(i) === 'default font';
    if (items.some(isDef)) {
      const fam = (cs.fontFamily || '').split(',')[0].replace(/["']/g, '').trim().toLowerCase();
      want = items.find(i => label(i) === fam) || items.find(isDef);
    } else if (items.some(i => /^[0-9,]+ pt/.test(label(i)))) {
      const pt = Math.round(parseFloat(cs.fontSize) * 0.75 * 2) / 2;
      const txt = String(pt).replace('.', ',');
      want = items.find(i => label(i).replace(' (normal)', '').replace(/ pt$/, '') === txt);
    }
    if (want) { want.classList.add('q-item--active'); want.style.fontWeight = '700'; want.style.background = 'rgba(10,102,232,.10)'; }
  };
  new MutationObserver(ms => {
    for (const m of ms) for (const a of m.addedNodes) {
      if (a.nodeType !== 1) continue;
      const menu = a.classList && a.classList.contains('q-menu') ? a : (a.querySelector && a.querySelector('.q-menu'));
      if (menu) setTimeout(() => marks(menu), 0);
    }
  }).observe(document.documentElement, {childList: true, subtree: true});
  // taille du texte en points (comme Outlook) : execCommand ne connaît que 7 niveaux ; on s'en sert comme repère puis on remplace
  // chaque <font size=7> par un <span> à la taille exacte. Sans sélection, la taille s'applique à ce qu'on tape ensuite.
  window.__pt = 11;
  const fixSizes = c => {
    const fonts = c.querySelectorAll('font[size="7"]');
    fonts.forEach(f => {
      const s = document.createElement('span'); s.style.fontSize = window.__pt + 'pt';
      while (f.firstChild) s.appendChild(f.firstChild);
      s.querySelectorAll('[style*="font-size"]').forEach(e => { e.style.fontSize = ''; });
      f.replaceWith(s);
    });
    return fonts.length;
  };
  window.mailiaSize = pt => {
    const n = edRange && (edRange.startContainer.nodeType === 1 ? edRange.startContainer : edRange.startContainer.parentElement);
    const c = n && n.closest && n.closest('.q-editor__content');
    if (!c) return;
    c.focus();
    const s = getSelection(); s.removeAllRanges(); s.addRange(edRange);
    window.__pt = pt;
    document.execCommand('styleWithCSS', false, false);
    document.execCommand('fontSize', false, '7');
    fixSizes(c);
    c.dispatchEvent(new Event('input', {bubbles: true}));
  };
  document.addEventListener('input', e => {
    const c = e.target && e.target.closest && e.target.closest('.q-editor__content');
    if (c && c.querySelector('font[size="7"]') && fixSizes(c)) c.dispatchEvent(new Event('input', {bubbles: true}));
  });
  // interligne : appliqué aux paragraphes touchés par la sélection (execCommand ne sait pas le faire)
  window.mailiaLine = v => {
    const n = edRange && (edRange.startContainer.nodeType === 1 ? edRange.startContainer : edRange.startContainer.parentElement);
    const c = n && n.closest && n.closest('.q-editor__content');
    if (!c) return;
    const blocks = [...c.querySelectorAll('p,div,li,h1,h2,h3,h4,h5,h6,blockquote')].filter(b => !b.closest('.msig') && edRange.intersectsNode(b));
    const leaves = blocks.filter(b => !blocks.some(o => o !== b && b.contains(o)));
    (leaves.length ? leaves : [c]).forEach(b => { b.style.lineHeight = v === 1.15 ? '' : String(v); });
    c.dispatchEvent(new Event('input', {bubbles: true}));
  };
  // image depuis un fichier : réduite puis insérée à l'endroit du curseur de l'éditeur actif
  window.mailiaAddImage = async (f, rootId) => {
    try {
      const bmp = await createImageBitmap(f), k = Math.min(1, 1100 / Math.max(bmp.width, bmp.height));
      const cv = document.createElement('canvas'); cv.width = Math.round(bmp.width * k); cv.height = Math.round(bmp.height * k);
      cv.getContext('2d').drawImage(bmp, 0, 0, cv.width, cv.height);
      const png = f.type === 'image/png' && f.size < 600000;
      window.mailiaInsert(rootId, '<img src="' + cv.toDataURL(png ? 'image/png' : 'image/jpeg', .85) + '" style="max-width:100%">');
    } catch (err) { /* image illisible : on ignore */ }
  };
  window.mailiaPickImage = () => {
    const n = edRange && (edRange.startContainer.nodeType === 1 ? edRange.startContainer : edRange.startContainer.parentElement);
    const root = (n && n.closest && n.closest('.q-editor')) || document.querySelector('.q-editor');
    if (!root) return;
    const inp = document.createElement('input'); inp.type = 'file'; inp.accept = 'image/*'; inp.multiple = true;
    inp.addEventListener('change', async () => { for (const f of inp.files) await window.mailiaAddImage(f, root.id); });
    inp.click();
  };
  // dictée : un repère (span.dictlive) est posé à l'endroit du curseur au début ; le texte définitif s'écrit juste avant lui, le provisoire (gris) à l'intérieur.
  // Le repère fait partie du contenu : il survit si l'éditeur est redessiné (le curseur, lui, repartirait en début de texte) et un long silence ne change rien.
  // Même emplacement de départ que mailiaInsert (au-dessus de la signature et du message cité), mais le DOM est modifié directement : insertHTML supprimerait la marque.
  // mode : « start » (pose le repère), « partial », « final », « end » (retire le repère, le texte provisoire restant devient normal).
  window.mailiaAgendaScroll = () => {
    const sc = document.querySelector('.ag-scroll');
    const t = document.querySelector('.ag-today');
    if (!sc || sc.dataset.done) return;
    sc.dataset.done = '1';
    const now = document.querySelector('.ag-now');
    if (now) sc.scrollTop = Math.max(0, now.offsetTop - 120);
  };
  document.addEventListener('click', e => {
    const col = e.target.closest && e.target.closest('.ag-col');
    if (!col || e.target.closest('.ag-ev')) return;
    const r = col.getBoundingClientRect();
    const rh = +col.dataset.rh, h0 = +col.dataset.h0;
    const min = h0 * 60 + Math.floor((e.clientY - r.top) / (rh / 2)) * 30;
    const root = document.querySelector('.root');
    if (root) root.dispatchEvent(new CustomEvent('agslot', {detail: {day: col.dataset.day, min: min}}));
  });
  window.mailiaLive = (htmlId, text, mode) => {
    const root = document.getElementById(htmlId);
    const c = root && root.querySelector('.q-editor__content');
    if (!c) return;
    let sp = c.querySelector('span.dictlive');
    if (!sp) {
      if (mode === 'end' || (mode !== 'start' && !text)) return;
      const mark = c.querySelector('.msig, hr');
      const before = (a, b) => a && b && (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
      const inTail = r => mark && (mark === r.startContainer || mark.contains(r.startContainer) || before(mark, r.startContainer));
      let r;
      if (edRange && c.contains(edRange.startContainer) && !inTail(edRange)) r = edRange.cloneRange();
      else {
        r = document.createRange();
        if (mark) {
          const prev = mark.previousElementSibling;
          if (prev && !prev.textContent.trim() && !prev.querySelector('img,table')) { r.selectNodeContents(prev); r.collapse(true); }
          else { r.setStartBefore(mark); r.collapse(true); }
        } else { r.selectNodeContents(c); r.collapse(false); }
      }
      r.deleteContents();
      sp = document.createElement('span'); sp.className = 'dictlive'; sp.style.color = '#6b7280';
      r.insertNode(sp);
    }
    // espace à ajouter devant le texte : seulement s'il suit un mot
    const pv = sp.previousSibling;
    const pre = pv && pv.nodeType === 3 ? pv.textContent : '';
    const lead = pre && !pre.endsWith(' ') && !pre.endsWith(String.fromCharCode(160)) ? ' ' : '';
    if (mode === 'start') return;
    if (mode === 'partial') { sp.textContent = text ? lead + text : ''; return; }
    if (mode === 'end') {
      const left = sp.textContent;  // texte provisoire jamais confirmé (arrêt brutal) : on le garde plutôt que de le perdre
      if (left) sp.before(document.createTextNode(left + ' '));
      const r2 = document.createRange(); r2.setStartBefore(sp); r2.collapse(true);
      sp.remove();
      const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r2); edRange = r2.cloneRange();
    } else {
      if (text) sp.before(document.createTextNode(lead + text + ' '));
      sp.textContent = '';
    }
    c.dispatchEvent(new Event('input', {bubbles: true}));
  };
  // Apple Intelligence : le texte à corriger est la sélection, sinon la partie écrite par l'utilisateur (au-dessus de la signature et du message cité)
  const fragText = frag => {
    const d = document.createElement('div');
    d.style.cssText = 'position:absolute;left:-9999px;top:0;white-space:pre-wrap;width:600px';
    d.appendChild(frag); document.body.appendChild(d);
    const t = d.innerText; d.remove(); return t;
  };
  window.mailiaTarget = htmlId => {
    const root = document.getElementById(htmlId);
    const c = root && root.querySelector('.q-editor__content');
    if (!c) return null;
    const sel = getSelection();
    let r, kind;
    if (sel.rangeCount && !sel.isCollapsed && c.contains(sel.anchorNode)) { r = sel.getRangeAt(0).cloneRange(); kind = 'selection'; }
    else {
      r = document.createRange(); r.selectNodeContents(c);
      const mark = c.querySelector('.msig, hr, blockquote');
      if (mark) r.setEndBefore(mark);
      kind = 'message';
    }
    window.__aiRange = r; window.__aiRoot = c; window.__aiKind = kind; window.__aiText = r.toString();
    return {text: fragText(r.cloneContents()).replace(/\\n{3,}/g, '\\n\\n').trim(), kind: kind};
  };
  window.mailiaApply = (htmlId, text) => {
    const root = document.getElementById(htmlId);
    const c = root && root.querySelector('.q-editor__content');
    const r = window.__aiRange;
    if (!c || !r || window.__aiRoot !== c || !c.contains(r.startContainer) || r.toString() !== window.__aiText) return false;  // le texte a changé depuis : on n'écrase rien
    c.focus();
    const s = getSelection(); s.removeAllRanges(); s.addRange(r);
    const esc = t => t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const lines = text.split('\\n').map(esc);
    // message entier : un bloc par ligne puis une ligne vide avant la signature ; sélection : simples retours à la ligne
    document.execCommand('insertHTML', false, window.__aiKind === 'message' ? lines.map(l => '<div>' + (l || '<br>') + '</div>').join('') + '<div><br></div>' : lines.join('<br>'));  // insertHTML : la touche « annuler » de l'éditeur fonctionne
    c.dispatchEvent(new Event('input', {bubbles: true}));
    window.__aiRange = null;
    return true;
  };
  window.mailiaInsert = (htmlId, html) => {
    const root = document.getElementById(htmlId);
    const c = root && root.querySelector('.q-editor__content');
    if (!c) return;
    c.focus();
    const s = getSelection();
    // le texte va dans la partie de l'utilisateur, au-dessus de la signature et du message cité : jamais à l'intérieur ni après
    const mark = c.querySelector('.msig, hr');
    const before = (a, b) => a && b && (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
    const inTail = r => mark && (mark === r.startContainer || mark.contains(r.startContainer) || before(mark, r.startContainer));
    if (edRange && c.contains(edRange.startContainer) && !inTail(edRange)) { s.removeAllRanges(); s.addRange(edRange); }
    else {
      const r = document.createRange();
      if (mark) {
        let prev = mark.previousElementSibling;
        if (prev && !prev.textContent.trim() && !prev.querySelector('img,table')) { r.selectNodeContents(prev); r.collapse(true); }
        else { r.setStartBefore(mark); r.collapse(true); }
      } else { r.selectNodeContents(c); r.collapse(false); }
      s.removeAllRanges(); s.addRange(r);
    }
    document.execCommand('insertHTML', false, html);
    c.dispatchEvent(new Event('input', {bubbles: true}));
  };
  // fichiers déposés sur la rédaction -> pièces jointes ; image collée dans le texte -> image incorporée.
  // Partout ailleurs un dépôt est neutralisé : sinon la fenêtre ouvrirait le fichier à la place de l'appli.
  const hasFiles = e => e.dataTransfer && [...(e.dataTransfer.types || [])].includes('Files');
  const composerEl = () => document.querySelector('.composer');
  let dragDepth = 0;
  document.addEventListener('dragenter', e => { if (!hasFiles(e)) return; e.preventDefault(); dragDepth++; const c = composerEl(); if (c) c.classList.add('dropping'); });
  document.addEventListener('dragleave', e => { if (!hasFiles(e)) return; dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) { const c = composerEl(); if (c) c.classList.remove('dropping'); } });
  document.addEventListener('dragover', e => { if (hasFiles(e)) e.preventDefault(); });
  document.addEventListener('drop', e => {
    if (!hasFiles(e)) return;
    e.preventDefault(); dragDepth = 0;
    const c = composerEl(); if (c) c.classList.remove('dropping');
    if (!c || !window.__mailiaUp) return;
    const up = getElement(window.__mailiaUp);
    const files = [...e.dataTransfer.files].filter(f => f.size > 0);
    if (up && up.$refs.qRef && files.length) up.$refs.qRef.addFiles(files);
  });
  document.addEventListener('paste', async e => {
    const content = e.target && e.target.closest && e.target.closest('.q-editor__content');
    if (!content) return;
    const imgs = [...((e.clipboardData && e.clipboardData.files) || [])].filter(f => f.type.startsWith('image/'));
    if (!imgs.length) return;
    e.preventDefault();
    const rootId = content.closest('.q-editor').id;
    for (const f of imgs) await window.mailiaAddImage(f, rootId);
  });
  // raccourcis clavier : ignorés quand on écrit, ou qu'une fenêtre est ouverte
  document.addEventListener('keydown', e => {
    if (e.metaKey || e.ctrlKey || e.altKey || e.isComposing) return;
    const t = e.target, tag = (t && t.tagName || '').toLowerCase();
    if (['input', 'textarea', 'select'].includes(tag) || (t && t.isContentEditable)) return;
    if ([...document.querySelectorAll('.q-dialog .q-card, .q-menu')].some(c => c.getBoundingClientRect().width > 0)) return;
    const KEYS = ['j', 'k', 'e', 'r', 'a', '#', 'Delete', 'p', 'u', 's', 't', 'c', '/', '?', 'Escape', 'ArrowDown', 'ArrowUp'];
    if (!KEYS.includes(e.key)) return;
    if (e.key === '/') { const i = document.querySelector('.lhead input'); if (i) { e.preventDefault(); i.focus(); } return; }
    if (['ArrowDown', 'ArrowUp'].includes(e.key) && !document.querySelector('.mrow.sel')) return;
    e.preventDefault();
    emitEvent('mailkey', {key: e.key});
  });
  // barre latérale masquable : le bord gauche de l'écran la fait réapparaître.
  // L'état vit sur <html> (s-hide, s-peek, s-auto) : l'élément principal, lui, est réécrit par l'application à chaque ouverture de message.
  let sideTimer = null;
  const S = document.documentElement;
  const sideRoot = () => S;
  const has = c => S.classList.contains(c);
  const setSideHidden = (r, v, persist = true) => {  // persist = false : masquage automatique, pas un choix à retenir
    S.classList.toggle('s-hide', v);
    if (!v) S.classList.remove('s-peek');
    const root = document.querySelector('.root');
    if (persist && root) root.dispatchEvent(new CustomEvent('sidestate', {detail: {hidden: v}}));
  };
  window.mailiaSide = a => {
    clearTimeout(sideTimer); sideTimer = null;
    if (a === 'hide') { S.classList.remove('s-peek'); setSideHidden(S, true); }
    else setSideHidden(S, false);
  };
  // case décochée pendant que la colonne n'est ouverte que provisoirement : on la remet en place
  window.mailiaAuto = on => {
    S.classList.toggle('s-auto', !!on);
    if (!on && has('s-hide') && has('s-peek')) setSideHidden(S, false);
  };
  window.mailiaSideInit = (hidden, auto) => {
    S.classList.toggle('s-auto', !!auto);
    S.classList.toggle('s-hide', !!hidden && !auto);
    S.classList.remove('s-peek');
  };
  // position réelle de la souris : le dernier élément survolé peut être périmé quand la colonne glisse sous un pointeur immobile
  let lastX = -1;
  const trackX = e => { lastX = e.clientX; };
  document.addEventListener('mousemove', trackX, true);
  document.addEventListener('dragover', trackX, true);
  const pointerOverSide = () => {
    const s = document.querySelector('.side');
    return lastX >= 0 && s && lastX <= Math.max(s.getBoundingClientRect().right, 8);
  };
  const SIDE_ZONE = '.side, .sidehot, .resizer.r1';
  const sideTouch = el => {
    if (!el || !el.closest) return;
    const hidden = has('s-hide');
    if (el.closest(SIDE_ZONE) || document.body.classList.contains('resizing')) {
      clearTimeout(sideTimer); sideTimer = null;
      if (hidden && el.closest('.side, .sidehot')) { if (has('s-auto')) S.classList.add('s-peek'); else setSideHidden(S, false); }
    } else if (!el.closest('.q-dialog, .q-menu, .q-notifications')) {
      const active = hidden ? has('s-peek') : has('s-auto');
      if (active && !sideTimer) sideTimer = setTimeout(() => {
        sideTimer = null;
        if (pointerOverSide() || document.body.classList.contains('resizing')) return;
        if (has('s-hide')) S.classList.remove('s-peek'); else if (has('s-auto')) setSideHidden(S, true, false);
      }, 450);
    }
  };
  document.addEventListener('mouseover', e => sideTouch(e.target));
  document.addEventListener('dragover', e => sideTouch(e.target));
  document.documentElement.addEventListener('mouseleave', () => { lastX = -1; sideTouch(document.body); });
  // colonnes redimensionnables (barre latérale | liste | lecture)
  const DEF = {side: 240, list: 400}, LIM = {side: [170, 420], list: [270, 760]}, MIN_READER = 380;
  let rz = null;
  const rootOf = el => el.closest('.root');
  const cssNum = (root, name, fallback) => parseFloat(root.style.getPropertyValue(name)) || fallback;
  document.addEventListener('pointerdown', e => {
    const h = e.target.closest && e.target.closest('.resizer');
    if (!h) return;
    e.preventDefault();
    rz = {h, root: rootOf(h), which: h.classList.contains('r1') ? 'side' : 'list'};
    h.classList.add('drag'); document.body.classList.add('resizing');
    try { h.setPointerCapture(e.pointerId); } catch (_) {}
  }, true);
  document.addEventListener('pointermove', e => {
    if (!rz) return;
    const box = rz.root.getBoundingClientRect(), side = (S.classList.contains('s-hide') && !S.classList.contains('s-peek')) ? 0 : cssNum(rz.root, '--w-side', DEF.side);
    if (rz.which === 'side') {
      const list = cssNum(rz.root, '--w-list', DEF.list);
      const v = Math.max(LIM.side[0], Math.min(LIM.side[1], e.clientX - box.left, box.width - list - MIN_READER));
      rz.root.style.setProperty('--w-side', v + 'px');
    } else {
      const v = Math.max(LIM.list[0], Math.min(LIM.list[1], e.clientX - box.left - side, box.width - side - MIN_READER));
      rz.root.style.setProperty('--w-list', v + 'px');
    }
  }, true);
  const endResize = () => {
    if (!rz) return;
    const {h, root} = rz; rz = null;
    h.classList.remove('drag'); document.body.classList.remove('resizing');
    root.dispatchEvent(new CustomEvent('colresize', {detail: {side: cssNum(root, '--w-side', DEF.side), list: cssNum(root, '--w-list', DEF.list)}}));
  };
  document.addEventListener('pointerup', endResize, true);
  document.addEventListener('pointercancel', endResize, true);
  document.addEventListener('dblclick', e => {  // double-clic sur une poignée : largeur d'origine
    const h = e.target.closest && e.target.closest('.resizer');
    if (!h) return;
    const root = rootOf(h), which = h.classList.contains('r1') ? 'side' : 'list';
    root.style.setProperty('--w-' + which, DEF[which] + 'px');
    root.dispatchEvent(new CustomEvent('colresize', {detail: {side: cssNum(root, '--w-side', DEF.side), list: cssNum(root, '--w-list', DEF.list)}}));
  });
  // insertion d'un tableau : on choisit le nombre de colonnes et de lignes (grille au survol, ou saisie)
  (() => {
    let pick = null;
    const close = () => { if (pick) { pick.remove(); pick = null; } };
    const tableHtml = (cols, rows) => {
      const w = (100 / cols).toFixed(2) + '%';
      const row = '<tr>' + `<td style="width:${w}"><br></td>`.repeat(cols) + '</tr>';
      return '<table border="1" cellpadding="6" style="border-collapse:collapse;width:100%"><tbody>' + row.repeat(rows) + '</tbody></table><p><br></p>';
    };
    const open = btn => {
      close();
      const ed = btn.closest('.q-editor'); if (!ed) return;
      const COLS = 10, ROWS = 8;
      pick = document.createElement('div'); pick.className = 'tblpick';
      pick.innerHTML = '<div class="pg"></div><div class="pl">Insérer un tableau 3 × 3</div>' +
        '<div class="pr"><label>Colonnes <input type="number" min="1" max="20" value="3" data-k="c"></label>' +
        '<label>Lignes <input type="number" min="1" max="50" value="3" data-k="r"></label><button data-k="ok">Insérer</button></div>';
      const grid = pick.querySelector('.pg'), lbl = pick.querySelector('.pl');
      grid.style.gridTemplateColumns = `repeat(${COLS}, 22px)`;
      for (let r = 1; r <= ROWS; r++) for (let c = 1; c <= COLS; c++) {
        const d = document.createElement('div'); d.dataset.c = c; d.dataset.r = r; grid.appendChild(d);
      }
      const mark = (c, r) => {
        grid.querySelectorAll('div').forEach(d => d.classList.toggle('on', +d.dataset.c <= c && +d.dataset.r <= r));
        lbl.textContent = `Insérer un tableau ${c} × ${r}`;
      };
      mark(3, 3);
      const insert = (c, r) => {
        c = Math.max(1, Math.min(20, c | 0)); r = Math.max(1, Math.min(50, r | 0));
        close(); window.mailiaInsert(ed.id, tableHtml(c, r));
      };
      grid.addEventListener('mouseover', e => { const d = e.target.closest('div[data-c]'); if (d) mark(+d.dataset.c, +d.dataset.r); });
      grid.addEventListener('click', e => { const d = e.target.closest('div[data-c]'); if (d) insert(+d.dataset.c, +d.dataset.r); });
      pick.querySelector('[data-k="ok"]').addEventListener('click', () =>
        insert(+pick.querySelector('[data-k="c"]').value, +pick.querySelector('[data-k="r"]').value));
      pick.addEventListener('keydown', e => { if (e.key === 'Enter') pick.querySelector('[data-k="ok"]').click(); });
      pick.addEventListener('mousedown', e => { if (!e.target.closest('input')) e.preventDefault(); });
      document.body.appendChild(pick);
      const r = btn.getBoundingClientRect();
      pick.style.left = Math.max(6, Math.min(r.left, innerWidth - pick.offsetWidth - 6)) + 'px';
      pick.style.top = Math.min(r.bottom + 6, innerHeight - pick.offsetHeight - 6) + 'px';
    };
    document.addEventListener('click', e => {
      const b = e.target.closest && e.target.closest('.q-editor__toolbar button, .q-editor .q-btn');
      const icon = b && b.querySelector('.q-icon');
      if (b && icon && icon.textContent.trim() === 'table_chart') { e.preventDefault(); e.stopImmediatePropagation(); open(b); return; }
      if (pick && !e.target.closest('.tblpick')) close();
    }, true);
    document.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });
  })();
  // images dans l'éditeur : un clic la sélectionne, on la déplace en la glissant, on la redimensionne par son coin
  (() => {
    const ED = '.q-editor__content';
    let img = null, box = null, hnd = null, bar = null, mark = null, drag = null;
    const fire = () => {  // l'éditeur peut remplacer ses éléments après une modification : on retrouve l'image par son rang
      const c = img && img.closest(ED); if (!c) return;
      const idx = Array.from(c.querySelectorAll('img')).indexOf(img);
      c.dispatchEvent(new Event('input', {bubbles: true}));
      setTimeout(() => {
        if (img && !img.isConnected && idx >= 0) { const n = c.querySelectorAll('img')[idx]; if (n) { img = n; place(); } }
      }, 200);
    };
    const make = () => {
      box = document.createElement('div'); box.className = 'mimg-box';
      hnd = document.createElement('div'); hnd.className = 'mimg-h'; box.appendChild(hnd);
      mark = document.createElement('div'); mark.className = 'mimg-drop';
      bar = document.createElement('div'); bar.className = 'tblbar mimg-bar';
      bar.innerHTML = `
        <span class="tt">Image</span>
        <label>Largeur <input data-k="w" type="number" min="8" max="1600" step="4"> px</label>
        <button data-k="l" title="Image à gauche, le texte l'entoure">gauche</button>
        <button data-k="c" title="Image centrée">centré</button>
        <button data-k="r" title="Image à droite, le texte l'entoure">droite</button>
        <button data-k="i" title="Image dans le texte">dans le texte</button>
        <button data-k="all" title="Remet en ligne, dans le texte, toutes les images de cette cellule">tout sur une ligne</button>
        <button data-k="del" class="danger" title="Supprimer l'image">supprimer</button>
        <span class="hint">Glissez l'image pour la déplacer · tirez le coin pour la redimensionner</span>`;
      document.body.append(box, mark, bar);
      bar.addEventListener('mousedown', e => { if (!e.target.closest('input')) e.preventDefault(); });
      const q = k => bar.querySelector('[data-k="' + k + '"]');
      q('w').addEventListener('input', () => { if (img) { setW(+q('w').value); } });
      const align = a => {
        if (!img) return;
        img.style.float = ''; img.style.display = ''; img.style.margin = ''; img.style.verticalAlign = ''; img.removeAttribute('align');
        if (a === '') img.style.verticalAlign = 'middle';
        if (a === 'l') { img.style.float = 'left'; img.style.margin = '0 10px 6px 0'; }
        if (a === 'r') { img.style.float = 'right'; img.style.margin = '0 0 6px 10px'; }
        if (a === 'c') { img.style.display = 'block'; img.style.margin = '6px auto'; }
        fire(); place();
      };
      q('l').addEventListener('click', () => align('l')); q('r').addEventListener('click', () => align('r'));
      q('c').addEventListener('click', () => align('c')); q('i').addEventListener('click', () => align(''));
      q('all').addEventListener('click', () => {
        if (!img) return;
        const scope = img.closest('td,th') || img.closest(ED);
        scope.querySelectorAll('img').forEach(i => { i.style.float = ''; i.style.display = ''; i.style.margin = ''; i.style.verticalAlign = 'middle'; i.removeAttribute('align'); });
        fire(); place();
      });
      q('del').addEventListener('click', () => { if (img) { const c = img.closest(ED); img.remove(); unselect(); if (c) c.dispatchEvent(new Event('input', {bubbles: true})); } });
    };
    const setW = (w, notify = true) => {
      if (!img || !(w > 0)) return;
      img.style.width = Math.round(w) + 'px'; img.style.height = 'auto';
      img.removeAttribute('width'); img.removeAttribute('height');
      if (notify) fire();
      place(false);
    };
    const place = (syncInput = true) => {
      if (!img || !img.isConnected) return unselect();
      const r = img.getBoundingClientRect();
      Object.assign(box.style, {display: 'block', left: r.left + 'px', top: r.top + 'px', width: r.width + 'px', height: r.height + 'px'});
      const bh = bar.offsetHeight || 40;
      bar.style.display = 'flex';
      let top = r.bottom + 8; if (top + bh > innerHeight - 6) top = Math.max(6, r.top - bh - 8);
      bar.style.top = top + 'px';
      bar.style.left = Math.max(6, Math.min(r.left, innerWidth - bar.offsetWidth - 6)) + 'px';
      const wi = bar.querySelector('[data-k="w"]');
      if (syncInput && document.activeElement !== wi) wi.value = Math.round(r.width);
    };
    const unselect = () => { img = null; if (box) { box.style.display = 'none'; bar.style.display = 'none'; mark.style.display = 'none'; } };
    const select = el => {
      if (!box) make();
      img = el;
      const ed = el.closest(ED); if (ed) ed.focus();
      const rg = document.createRange(); rg.selectNode(el);
      const s = getSelection(); s.removeAllRanges(); s.addRange(rg);
      place();
    };
    const caretAt = (x, y) => {
      if (document.caretRangeFromPoint) return document.caretRangeFromPoint(x, y);
      const p = document.caretPositionFromPoint && document.caretPositionFromPoint(x, y);
      if (!p) return null; const r = document.createRange(); r.setStart(p.offsetNode, p.offset); r.collapse(true); return r;
    };
    document.addEventListener('mousedown', e => {
      if (e.button !== 0) return;
      if (e.target === hnd) {
        e.preventDefault(); e.stopPropagation();
        drag = {kind: 'size', x0: e.clientX, w0: img.getBoundingClientRect().width}; document.body.style.userSelect = 'none'; return;
      }
      const t = e.target.closest && e.target.closest(ED + ' img');
      if (t) {
        e.preventDefault();
        select(t);
        drag = {kind: 'move', x0: e.clientX, y0: e.clientY, moved: false, range: null};
        document.body.style.userSelect = 'none';
      }
    }, true);
    document.addEventListener('mousemove', e => {
      if (!drag) return;
      if (drag.kind === 'size') { setW(Math.max(8, drag.w0 + e.clientX - drag.x0), false); return; }
      if (!drag.moved && Math.hypot(e.clientX - drag.x0, e.clientY - drag.y0) < 5) return;
      drag.moved = true; document.body.style.cursor = 'grabbing';
      const rg = caretAt(e.clientX, e.clientY);
      drag.range = rg && rg.startContainer && rg.startContainer.parentElement && rg.startContainer.parentElement.closest(ED) && !img.contains(rg.startContainer) ? rg : null;
      if (drag.range) {
        const rc = drag.range.getClientRects()[0] || (drag.range.startContainer.nodeType === 1 ? drag.range.startContainer.getBoundingClientRect() : null);
        if (rc) Object.assign(mark.style, {display: 'block', left: rc.left + 'px', top: rc.top + 'px', height: Math.max(rc.height, 16) + 'px'});
      } else mark.style.display = 'none';
    });
    document.addEventListener('mouseup', () => {
      if (!drag) return;
      const d = drag; drag = null; document.body.style.userSelect = ''; document.body.style.cursor = '';
      mark && (mark.style.display = 'none');
      if (d.kind === 'move' && d.moved && d.range && img) { d.range.insertNode(img); fire(); select(img); }
      else if (d.kind === 'size') { fire(); }
    });
    document.addEventListener('click', e => {
      if (!img) return;
      if (e.target.closest && (e.target.closest(ED + ' img') || e.target.closest('.mimg-bar') || e.target === hnd)) return;
      unselect();
    });
    document.addEventListener('keydown', e => { if (e.key === 'Escape') unselect(); });
    window.addEventListener('scroll', () => place(false), true);
    window.addEventListener('resize', () => place(false));
  })();
  // tableaux dans l'éditeur : colonnes et lignes se redimensionnent à la souris, barre de propriétés (bordures, fond)
  (() => {
    const ED = '.q-editor__content';
    let tbl = null, cell = null, bar = null, drag = null, hover = null;
    const fire = () => {  // l'éditeur peut remplacer ses éléments après une modification : on retrouve le tableau et la cellule par leur rang
      const c = tbl && tbl.closest(ED); if (!c) return;
      const ti = Array.from(c.querySelectorAll('table')).indexOf(tbl);
      const ri = cell && cell.parentElement ? cell.parentElement.rowIndex : 0, ci = cell ? cell.cellIndex : 0;
      c.dispatchEvent(new Event('input', {bubbles: true}));
      setTimeout(() => {
        if (tbl && !tbl.isConnected && ti >= 0) {
          const t = c.querySelectorAll('table')[ti];
          if (t && t.rows.length) { tbl = t; cell = (t.rows[ri] || t.rows[0]).cells[ci] || t.rows[0].cells[0]; place(); }
        }
      }, 200);
    };
    const cellsOf = t => Array.from(t.rows).flatMap(r => Array.from(r.cells));
    const startCol = c => { let i = 0; for (const x of c.parentElement.cells) { if (x === c) break; i += x.colSpan || 1; } return i; };
    const colCount = t => Math.max(1, ...Array.from(t.rows).map(r => Array.from(r.cells).reduce((n, c) => n + (c.colSpan || 1), 0)));
    const colWidths = t => {  // largeur mesurée de chaque colonne
      const n = colCount(t), out = new Array(n).fill(0);
      for (const c of cellsOf(t)) if ((c.colSpan || 1) === 1) { const i = startCol(c); if (!out[i]) out[i] = c.getBoundingClientRect().width; }
      const known = out.filter(Boolean), avg = known.length ? known.reduce((a, b) => a + b, 0) / known.length : t.getBoundingClientRect().width / n;
      return out.map(v => v || avg);
    };
    const setCols = (t, widths, unit) => {  // colgroup (navigateurs) + largeur des cellules (messageries qui ignorent <col>)
      let g = t.querySelector('colgroup');
      if (!g) { g = document.createElement('colgroup'); t.insertBefore(g, t.firstChild); }
      g.innerHTML = '';
      const total = widths.reduce((a, b) => a + b, 0);
      const val = w => unit === '%' ? (w / total * 100).toFixed(2) + '%' : Math.round(w) + 'px';
      widths.forEach(w => { const c = document.createElement('col'); c.style.width = val(w); g.appendChild(c); });
      for (const c of cellsOf(t)) if ((c.colSpan || 1) === 1) { c.removeAttribute('width'); c.style.width = val(widths[startCol(c)]); }
    };
    const freeze = t => {  // mise en page fixe : la largeur de chaque colonne devient celle qu'on voit
      const w = colWidths(t), total = w.reduce((a, b) => a + b, 0);
      t.style.tableLayout = 'fixed'; t.style.width = Math.round(total) + 'px'; t.removeAttribute('width');
      setCols(t, w, 'px');
      return w;
    };

    // ---- poignées : bord droit d'une cellule = colonne, bord bas = ligne ----
    const EDGE = 6;
    const handleAt = e => {
      const c = e.target.closest && e.target.closest('td,th');
      if (!c || !c.closest(ED)) return null;
      const r = c.getBoundingClientRect();
      if (r.right - e.clientX <= EDGE) return {kind: 'col', cell: c};
      if (r.bottom - e.clientY <= EDGE) return {kind: 'row', cell: c};
      return null;
    };
    document.addEventListener('mousemove', e => {
      if (drag) return;
      const ed = e.target.closest && e.target.closest(ED);
      if (!ed) return;
      hover = handleAt(e);
      ed.style.cursor = hover ? (hover.kind === 'col' ? 'col-resize' : 'row-resize') : '';
    });
    document.addEventListener('mousedown', e => {
      const h = handleAt(e);
      if (!h) return;
      e.preventDefault();
      const t = h.cell.closest('table'); tbl = t; cell = h.cell;
      if (h.kind === 'col') {
        const widths = freeze(t), i = startCol(h.cell) + (h.cell.colSpan || 1) - 1;
        drag = {kind: 'col', t, i, x0: e.clientX, widths};
      } else {
        drag = {kind: 'row', t, row: h.cell.parentElement, y0: e.clientY, h0: h.cell.getBoundingClientRect().height};
      }
      document.body.style.userSelect = 'none';
    }, true);
    document.addEventListener('mousemove', e => {
      if (!drag) return;
      if (drag.kind === 'col') {
        const w = drag.widths.slice(); w[drag.i] = Math.max(24, drag.widths[drag.i] + e.clientX - drag.x0);
        drag.t.style.width = Math.round(w.reduce((a, b) => a + b, 0)) + 'px';
        setCols(drag.t, w, 'px');
      } else {
        const h = Math.max(16, Math.round(drag.h0 + e.clientY - drag.y0));
        for (const c of drag.row.cells) c.style.height = h + 'px';
      }
      place();
    });
    document.addEventListener('mouseup', () => {
      if (!drag) return;
      drag = null; document.body.style.userSelect = '';
      fire(); syncBar();
    });

    // ---- barre de propriétés ----
    const el = (tag, attrs, html) => { const x = document.createElement(tag); Object.assign(x, attrs || {}); if (html) x.innerHTML = html; return x; };
    const build = () => {
      bar = el('div', {className: 'tblbar'});
      bar.innerHTML = `
        <span class="tt">Tableau</span>
        <label>Bordure <select data-k="w">
          <option value="none">Aucune</option><option value="clear">Transparente</option>
          <option value="1">Fine</option><option value="2">Moyenne</option><option value="4">Épaisse</option></select></label>
        <label>Trait <select data-k="s"><option value="solid">Continu</option><option value="dotted">Pointillé</option><option value="dashed">Tirets</option></select></label>
        <label>Couleur <input data-k="c" type="color" value="#888888"></label>
        <label>Fond <input data-k="bg" type="color" value="#ffffff"></label>
        <button data-k="bgx" title="Retirer le fond">sans fond</button>
        <label class="chk"><input data-k="one" type="checkbox"> cellule seule</label>
        <button data-k="fit" title="Le tableau prend toute la largeur">pleine largeur</button>
        <button data-k="del" class="danger" title="Supprimer le tableau">supprimer</button>`;
      document.body.appendChild(bar);
      bar.addEventListener('mousedown', e => { if (!e.target.closest('select,input')) e.preventDefault(); });
      const q = k => bar.querySelector('[data-k="' + k + '"]');
      const targets = () => (q('one').checked ? [cell] : cellsOf(tbl));
      const borders = () => {
        if (!tbl) return;
        const w = q('w').value, st = q('s').value, col = q('c').value;
        const css = w === 'none' ? '0' : w === 'clear' ? '1px solid transparent' : w + 'px ' + st + ' ' + col;
        tbl.removeAttribute('border');
        if (!q('one').checked) { tbl.style.borderCollapse = 'collapse'; tbl.style.border = css; }
        targets().forEach(c => { c.removeAttribute('border'); c.style.border = css; });
        fire();
      };
      ['w', 's', 'c'].forEach(k => q(k).addEventListener('input', borders));
      q('bg').addEventListener('input', () => { if (tbl) { targets().forEach(c => { c.removeAttribute('bgcolor'); c.style.backgroundColor = q('bg').value; }); fire(); } });
      q('bgx').addEventListener('click', () => { if (tbl) { targets().forEach(c => { c.removeAttribute('bgcolor'); c.style.backgroundColor = ''; }); fire(); } });
      q('fit').addEventListener('click', () => {
        if (!tbl) return;
        const w = colWidths(tbl); tbl.style.tableLayout = 'fixed'; tbl.style.width = '100%'; tbl.removeAttribute('width');
        setCols(tbl, w, '%'); fire(); place();
      });
      q('del').addEventListener('click', () => { if (tbl) { const t = tbl; hide(); const c = t.closest(ED); t.remove(); if (c) c.dispatchEvent(new Event('input', {bubbles: true})); } });
    };
    const syncBar = () => {  // reflète les bordures de la cellule active
      if (!bar || !cell) return;
      const cs = getComputedStyle(cell), q = k => bar.querySelector('[data-k="' + k + '"]');
      const bw = parseFloat(cs.borderTopWidth) || 0, bs = cs.borderTopStyle, bc = cs.borderTopColor;
      const clear = bc === 'rgba(0, 0, 0, 0)' || bc === 'transparent';
      q('w').value = (bs === 'none' || !bw) ? 'none' : clear ? 'clear' : bw <= 1.5 ? '1' : bw <= 3 ? '2' : '4';
      if (['solid', 'dotted', 'dashed'].includes(bs)) q('s').value = bs;
      const m = bc.replace(/[^0-9,]/g, '').split(',');
      if (m && !clear) q('c').value = '#' + m.slice(0, 3).map(n => (+n).toString(16).padStart(2, '0')).join('');
    };
    const place = () => {
      if (!bar || !tbl || !tbl.isConnected) return hide();
      const r = tbl.getBoundingClientRect(), bh = bar.offsetHeight || 40;
      let top = r.top - bh - 6; if (top < 6) top = Math.min(r.bottom + 6, innerHeight - bh - 6);
      bar.style.top = Math.max(6, top) + 'px';
      bar.style.left = Math.max(6, Math.min(r.left, innerWidth - bar.offsetWidth - 6)) + 'px';
    };
    const hide = () => { if (bar) bar.style.display = 'none'; tbl = cell = null; };
    const show = c => {
      if (!bar) build();
      cell = c; tbl = c.closest('table');
      bar.style.display = 'flex'; syncBar(); place();
    };
    const onPoint = e => {
      if (drag) return;
      const c = e.target.closest && e.target.closest('td,th');
      if (c && c.closest(ED)) show(c);
    };
    document.addEventListener('click', e => {
      if (e.target.closest && (e.target.closest('.tblbar') || e.target.closest(ED))) { onPoint(e); if (!(e.target.closest('td,th') && e.target.closest(ED)) && !e.target.closest('.tblbar')) hide(); return; }
      hide();
    });
    document.addEventListener('keyup', e => {
      const s = getSelection(); if (!s || !s.anchorNode) return;
      const n = s.anchorNode.nodeType === 1 ? s.anchorNode : s.anchorNode.parentElement;
      const c = n && n.closest && n.closest('td,th');
      if (c && c.closest(ED)) show(c); else if (bar && bar.style.display !== 'none' && n && !n.closest('.tblbar')) hide();
    });
    window.addEventListener('scroll', place, true);
    window.addEventListener('resize', place);
  })();
  // menu du clic droit dans la lecture d'un message (en français, au lieu du menu natif du système)
  (function () {
    let menu = null;
    const close = () => { if (menu) { menu.remove(); menu = null; } };
    const copy = t => {
      const ta = document.createElement('textarea'); ta.value = t; ta.style.cssText = 'position:fixed;opacity:0';
      document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove();
    };
    const key = k => emitEvent('mailkey', {key: k});
    const show = (x, y, o) => {
      close();
      const items = [];
      if (o.link) items.push(["Copier l'adresse du lien", () => copy(o.link)]);
      if (o.sel) items.push(['Copier', () => copy(o.sel)]);
      items.push(['Tout sélectionner', o.all]);
      items.push(null, ['Répondre', () => key('r')], ['Répondre à tous', () => key('a')], ['Transférer…', () => key('f')]);
      menu = document.createElement('div');
      menu.className = 'ctxm';
      for (const it of items) {
        if (!it) { const hr = document.createElement('div'); hr.className = 'ctxsep'; menu.appendChild(hr); continue; }
        const b = document.createElement('div');
        b.className = 'ctxit'; b.textContent = it[0];
        b.addEventListener('mousedown', ev => ev.preventDefault());
        b.addEventListener('click', ev => { ev.stopPropagation(); close(); it[1](); });
        menu.appendChild(b);
      }
      document.body.appendChild(menu);
      const r = menu.getBoundingClientRect();
      menu.style.left = Math.max(4, Math.min(x, innerWidth - r.width - 4)) + 'px';
      menu.style.top = Math.max(4, Math.min(y, innerHeight - r.height - 4)) + 'px';
    };
    window.mailiaCtx = show;
    document.addEventListener('contextmenu', e => {
      const t = e.target;
      if (!t.closest || !t.closest('.rbody') || t.closest('input,textarea,[contenteditable="true"],.q-editor,.composer')) return;
      e.preventDefault();
      const a = t.closest('a[href]');
      const box = t.closest('.mailbody,.tcard');
      show(e.clientX, e.clientY, {sel: String(getSelection()), link: a ? a.href : '', all: () => {
        const r = document.createRange(); r.selectNodeContents(box || t); const s = getSelection(); s.removeAllRanges(); s.addRange(r); }});
    });
    document.addEventListener('click', close);
    document.addEventListener('scroll', close, true);
    window.addEventListener('blur', close);
    document.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });
  })();
  // cadre isolé d'un mail HTML : il annonce sa hauteur et relaie les clics sur ses liens
  window.addEventListener('message', e => {
    const d = e.data;
    if (!d || !d.mailia) return;
    for (const f of document.querySelectorAll('iframe.mailframe')) {
      if (f.contentWindow !== e.source) continue;
      if (d.menu) {
        const b = f.getBoundingClientRect();
        window.mailiaCtx(b.left + d.x, b.top + d.y, {sel: d.sel, link: d.link, all: () => f.contentWindow.postMessage({mailia: 1, all: 1}, '*')});
      }
      if (d.h) f.style.height = Math.max(40, d.h + 2) + 'px';
      if (d.url) f.dispatchEvent(new CustomEvent('mailink', {detail: {url: d.url}}));
    }
  });
  // glisser-déposer d'un mail (ou d'une conversation) vers un dossier de la barre latérale
  let drag = null;
  document.addEventListener('dragstart', e => {
    const w = e.target.closest && e.target.closest('.swrap');
    if (!w || !w.dataset.id) return;
    closeOpen(null);
    drag = {id: w.dataset.id, acct: w.dataset.acct, folder: w.dataset.folder, el: w};
    e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', w.dataset.id);
    document.body.classList.add('dragging-mail'); w.classList.add('dragging');
    document.querySelectorAll('.droptarget').forEach(t => t.classList.toggle('valid', t.dataset.acct === drag.acct && t.dataset.folder !== drag.folder));
    document.querySelectorAll('.dp-group').forEach(g => g.style.display = g.dataset.acct === drag.acct ? 'block' : 'none');
  });
  const target = e => e.target.closest && e.target.closest('.droptarget.valid');
  document.addEventListener('dragover', e => { if (drag && target(e)) { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; } });
  document.addEventListener('dragenter', e => { const t = target(e); if (drag && t) t.classList.add('over'); });
  document.addEventListener('dragleave', e => { const t = target(e); if (t && !t.contains(e.relatedTarget)) t.classList.remove('over'); });
  document.addEventListener('drop', e => {
    const t = drag && target(e);
    if (!t) return;
    e.preventDefault(); t.classList.remove('over');
    t.dispatchEvent(new CustomEvent('dropmail', {detail: {id: drag.id}}));
  });
  document.addEventListener('dragend', () => {
    if (drag) drag.el.classList.remove('dragging');
    drag = null; document.body.classList.remove('dragging-mail');
    document.querySelectorAll('.droptarget').forEach(t => t.classList.remove('valid', 'over'));
  });
  // réorganisation des dossiers de la barre latérale (au même niveau seulement) : une ligne se dépose avant ou après une autre
  let dragF = null;
  const fclear = () => document.querySelectorAll('.ins-before, .ins-after').forEach(x => x.classList.remove('ins-before', 'ins-after'));
  const ftarget = e => e.target.closest && e.target.closest('.navfolder.fvalid');
  const fafter = (e, t) => { const r = t.getBoundingClientRect(); return e.clientY > r.top + r.height / 2; };
  document.addEventListener('dragstart', e => {
    const n = e.target.closest && e.target.closest('.navfolder');
    if (!n || !n.dataset.fkey) return;
    dragF = {el: n, acct: n.dataset.acct, key: n.dataset.fkey, parent: n.dataset.parent};
    e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', 'dossier');
    n.classList.add('dragging-f');
    document.querySelectorAll('.navfolder').forEach(x => x.classList.toggle('fvalid', x !== n && x.dataset.acct === dragF.acct && x.dataset.parent === dragF.parent));
  });
  document.addEventListener('dragover', e => {
    const t = dragF && ftarget(e);
    if (!t) return;
    e.preventDefault(); e.dataTransfer.dropEffect = 'move';
    fclear(); t.classList.add(fafter(e, t) ? 'ins-after' : 'ins-before');
  });
  document.addEventListener('drop', e => {
    const t = dragF && ftarget(e);
    if (!t) return;
    e.preventDefault();
    t.dispatchEvent(new CustomEvent('folderdrop', {detail: {from: dragF.key, after: fafter(e, t)}}));
    fclear();
  });
  document.addEventListener('dragend', () => {
    if (dragF) dragF.el.classList.remove('dragging-f');
    dragF = null; fclear();
    document.querySelectorAll('.fvalid').forEach(x => x.classList.remove('fvalid'));
  });
  // trackpad Mac : balayage horizontal à deux doigts sur une ligne
  document.addEventListener('wheel', e => {
    const w = e.target.closest('.swrap');
    if (!w || Math.abs(e.deltaX) < 2 || Math.abs(e.deltaX) < Math.abs(e.deltaY) * 1.5) return;
    e.preventDefault();
    if (ww !== w) { closeOpen(w); ww = w; wx = parseFloat(w.dataset.x || 0); }
    wx = Math.max(MAXL, Math.min(MAXR, wx - e.deltaX));
    setX(w, wx, false);
    clearTimeout(wt);
    wt = setTimeout(() => { const t = ww; ww = null; suppress = true; setTimeout(() => suppress = false, 60); settle(t); }, 160);
  }, {passive: false, capture: true});
})();
</script>"""

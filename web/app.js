/* DocTree local viewer. Repository contents are data, never HTML or executable code. */
'use strict';
const $ = (selector, root = document) => root.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const list = value => Array.isArray(value) ? value : value == null ? [] : [value];
const textValue = value => typeof value === 'string' ? value : value == null ? '' : typeof value === 'object' ? (value.summary || value.description || value.message || value.label || value.status || JSON.stringify(value)) : String(value);
const short = (value, length = 12) => { const s = textValue(value); return s.length > length ? s.slice(0, length) + '…' : s; };
const icons = {
  tree:'<path d="M12 3v5m-6 8v-5h12v5M6 11h6m0-3v8"/><rect x="9" y="2" width="6" height="5" rx="1.4"/><rect x="3" y="16" width="6" height="5" rx="1.4"/><rect x="15" y="16" width="6" height="5" rx="1.4"/>',
  home:'<path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-6v-7h-4v7H4a1 1 0 0 1-1-1Z"/>',
  folder:'<path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',
  file:'<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9Z"/><path d="M14 3v6h6M8 13h8m-8 4h5"/>',
  search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/>',
  chevron:'<path d="m9 5 7 7-7 7"/>',
  arrow:'<path d="M4 12h15m-5-5 5 5-5 5"/>',
  refresh:'<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 6a8 8 0 0 1 13 3M5 15a8 8 0 0 0 13 3"/>',
  check:'<path d="m5 12 4 4L19 6"/>',
  layers:'<path d="m12 3 10 5-10 5L2 8Zm-9 9 9 5 9-5m-18 5 9 5 9-5"/>',
  clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  download:'<path d="M12 3v12m-5-5 5 5 5-5M4 15v5h16v-5"/>',
  leaf:'<path d="M20 3c0 9-3 15-10 15a7 7 0 0 1-6-6C4 5 12 4 20 3ZM3 21l12-12"/>',
  flag:'<path d="M5 22V3c5-3 9 4 15 0v11c-6 4-10-3-15 0"/>',
  link:'<path d="m9 15 6-6M7 14l-2 2a4 4 0 0 0 6 6l3-3m-4-9 3-3a4 4 0 0 1 6 6l-2 2" transform="translate(0 -2)"/>',
  shield:'<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z"/><path d="m8 12 3 3 5-6"/>',
  menu:'<path d="M4 6h16M4 12h16M4 18h16"/>',
  spark:'<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z"/>',
  lab:'<path d="M9 3h6m-5 0v6L4 19a1 1 0 0 0 1 2h14a1 1 0 0 0 1-2L14 9V3M7 15h10"/>',
};
const icon = (name, size = 16, extra = '') => `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.55" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" ${extra}>${icons[name] || icons.file}</svg>`;
const ui = {data:null, selected:null, project:'all', query:'', changed:false, pending:false, tab:'overview', expanded:new Set(), mobile:false, busy:false, error:'', sourceRequest:0, searchRenderTimer:null};
try { ui.selected = localStorage.getItem('doctree.node') || null; } catch (_) { /* Local preferences are optional. */ }
const requestedLocation = new URLSearchParams(location.search);
const requestedNode = requestedLocation.get('node');
let resolveRequestedLocation = true;
if(requestedNode) ui.selected = requestedNode;

function nodes() { return ui.data?.nodes || {}; }
function allNodes() { return Object.values(nodes()); }
function getNode(id) { return nodes()[id]; }
function children(node) { return list(node?.children).map(item => typeof item === 'string' ? getNode(item) : getNode(item?.id)).filter(Boolean); }
function ancestry(node) { const items = []; const seen = new Set(); while(node && !seen.has(node.id)) { items.unshift(node); seen.add(node.id); node = getNode(node.parent); } return items; }
function descendants(node, seen = new Set()) { if (!node || seen.has(node.id)) return []; seen.add(node.id); return [node, ...children(node).flatMap(child => descendants(child, seen))]; }
function projects() {
  const configured = Array.isArray(ui.data?.projects) ? ui.data.projects : Object.values(ui.data?.projects || {});
  if (configured.length) return configured;
  return roots().map(node => ({id:node.project_id || node.id, title:node.title, root_node:node.id, source_type:node.source_type}));
}
function roots() { return allNodes().filter(node => !node.parent || !getNode(node.parent)); }
function projectRoot(project) {
  const direct = getNode(typeof project.root_node === 'object' ? project.root_node.id : project.root_node || project.root_id || project.node_id);
  return direct || allNodes().find(node => node.project_id === project.id && (!node.parent || getNode(node.parent)?.project_id !== project.id)) || getNode(project.id);
}
function sourceBadge(source) { const config = {sample:['sample','样例项目'],local_copy:['copy','本地副本'],external:['external','外部来源'],sidecar:['copy','旁路索引']}[source] || ['external',source || '来源待记录']; return `<span class="badge ${config[0]}">${esc(config[1])}</span>`; }
function reviewBadge(node) { return node.review === 'reviewed' ? '<span class="badge reviewed">已审阅</span>' : '<span class="badge candidate">候选 · 待审阅</span>'; }
function stateBadges(node) { return `${node.changed ? '<span class="badge changed">有变化</span>' : ''}${node.pending ? `<span class="badge pending">${node.summary_state==='candidate_current'?'汇总待审阅':'待汇总'}</span>` : ''}`; }
function nodeSummary(node) { return textValue(node?.summary) || textValue(node?.purpose) || '暂无摘要，展开节点查看文档与证据。'; }
function summaryCoverage(node) { const state=textValue(node.summary_state); if(state==='current_reviewed'||state==='reviewed_current')return '已覆盖 · 已审阅'; if(state==='current_candidate'||state==='candidate_current')return '已生成候选 · 待审阅'; if(state.includes('stale'))return '输入已变化'; if(node.pending && node.summary_inputs)return '汇总流程待完成'; return node.pending?'有输入待汇总':'无待汇总输入'; }
function summaryNotice(node) { if(!node.pending)return ''; const state=textValue(node.summary_state); const currentCandidate=state==='current_candidate'||state==='candidate_current'; return `<div class="stale-note">${currentCandidate?'候选摘要已覆盖当前输入，仍需审阅后才能完成此层汇总。':'汇总流程尚未完成：当前摘要可能尚未覆盖新变化，或仍在等待审阅。'}</div>`; }
function nodeTitle(node) { return textValue(node?.title || node?.id || '未命名节点'); }
function kindLabel(kind) { return ({project:'项目',workspace:'总入口',module:'模块',research:'研究',strategy:'策略',experiment:'实验',document:'文档',evidence:'证据',delivery:'交付',collection:'逻辑分组',topic:'课题'}[kind]) || kind || '节点'; }
function kindIcon(node) { return node.kind === 'experiment' ? 'lab' : node.kind === 'project' || node.kind === 'workspace' ? 'tree' : children(node).length ? 'folder' : 'file'; }
function matches(node) {
  if (ui.project !== 'all' && node.project_id !== ui.project && node.id !== ui.project) return false;
  if (ui.changed && !node.changed) return false;
  if (ui.pending && !node.pending) return false;
  const query = ui.query.trim().toLocaleLowerCase();
  return !query || [node.title,node.id,node.purpose,node.summary,node.entry].map(textValue).join(' ').toLocaleLowerCase().includes(query);
}
function relevant(node, seen = new Set()) { if (!node || seen.has(node.id)) return false; seen.add(node.id); return matches(node) || children(node).some(child => relevant(child, seen)); }
function activeFilters() { return ui.query.trim() || ui.changed || ui.pending || ui.project !== 'all'; }
function dateText(raw, compact = false) { if(!raw) return '时间未记录'; const date = new Date(raw); if(Number.isNaN(date.getTime())) return textValue(raw); return date.toLocaleString('zh-CN', compact ? {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false} : {year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}); }
function eventType(event) { const type = event.kind || event.type || event.action || event.event || '事件'; return ({scan_refreshed:'扫描已刷新',summary_committed:'摘要更新记录',delivery_imported:'导入任务交付',scan:'扫描完成',refresh:'重新扫描',delivery_imported:'导入任务交付',delivery:'任务交付',rollup:'生成候选汇总',rollup_written:'生成候选汇总',rollup_applied:'候选汇总已记录',rollup_generated:'生成候选汇总',viewed:'更新查看基线',baseline:'更新查看基线',baseline_marked:'更新查看基线',conflict:'检测到版本冲突',change:'发现节点变化',node_changed:'发现节点变化',review:'记录审阅结论',import_delivery:'导入任务交付'}[type]) || type; }
function eventNodeId(event) { return event.node_id || event.node || event.target || ''; }
function eventsFor(node) { const events = list(ui.data?.events).slice().reverse(); if (!node) return events; const ids = new Set(descendants(node).map(n=>n.id)); return events.filter(event => ids.has(eventNodeId(event)) || [...list(event.node_ids),...list(event.changed),...list(event.changed_nodes),...list(event.source_changed),...list(event.topology_changed),...list(event.added)].some(id=>ids.has(id))); }
function showToast(message, error = false) { const toast = $('#toast'); toast.textContent = message; toast.className = `toast visible${error?' error':''}`; clearTimeout(showToast.timer); showToast.timer = setTimeout(()=>toast.classList.remove('visible'), 4500); }
async function api(url, options = {}) { const response = await fetch(url, {headers:{'Content-Type':'application/json'},...options}); const result = await response.json().catch(()=>({error:`服务返回了无法读取的内容（${response.status}）`})); if(!response.ok || result.error) throw new Error(textValue(result.error || result.message || `请求失败（${response.status}）`)); return result; }
async function refreshState() {
  const response = await api('/api/state'); ui.data = response.state || response;
  if (Array.isArray(ui.data.nodes)) ui.data.nodes = Object.fromEntries(ui.data.nodes.map(node=>[node.id,node]));
  if(resolveRequestedLocation) {
    const projectId = requestedLocation.get('project'), entry = requestedLocation.get('entry');
    if(!requestedNode && projectId) {
      const match = allNodes().find(node=>node.project_id===projectId && node.entry===entry)
        || allNodes().find(node=>node.project_id===projectId && !node.parent);
      ui.selected = match?.id || null;
      ui.project = projectId;
    }
    resolveRequestedLocation = false;
  }
  if(ui.selected && !getNode(ui.selected)) ui.selected = null;
  if(!ui.expanded.size) roots().forEach(root=>ui.expanded.add(root.id));
  if(ui.selected) ancestry(getNode(ui.selected)).forEach(node=>ui.expanded.add(node.id));
  ui.error = '';
}
async function action(endpoint, success) { if(ui.busy) return; ui.busy = true; render(); try { const result=await api(endpoint,{method:'POST',body:'{}'}); await refreshState(); if(endpoint==='/api/rollup') { const count=list(result.results).length; const skipped=list(result.skipped).length; success=count?`本次处理 ${count} 份候选汇总，保留待审阅状态。`:'当前没有新的可生成候选汇总。'; if(skipped)success+=` 另有 ${skipped} 个节点仍待前置事项处理。`; } showToast(success); } catch(error) { ui.error = error.message; showToast(error.message,true); } finally { ui.busy=false; render(); } }

function renderTree(node, depth = 0, seen = new Set()) {
  if(!node || seen.has(node.id) || !relevant(node)) return ''; seen = new Set(seen); seen.add(node.id);
  const kids = children(node).filter(child=>relevant(child));
  const expanded = ui.expanded.has(node.id) || !!activeFilters();
  return `<div class="tree-item" role="treeitem" aria-selected="${node.id===ui.selected}" ${kids.length?`aria-expanded="${expanded}"`:''}><div class="tree-row depth-${Math.min(depth,12)}${node.id===ui.selected?' selected':''}"><button class="tree-toggle${expanded?' expanded':''}${kids.length?'':' empty'}" data-expand="${esc(node.id)}" aria-label="${expanded?'折叠':'展开'} ${esc(nodeTitle(node))}" tabindex="${kids.length?'0':'-1'}">${kids.length?icon('chevron',11):''}</button><button class="tree-name" data-node="${esc(node.id)}" data-testid="tree-node" title="${esc(nodeTitle(node))}"><span class="tree-icon">${icon(kindIcon(node),13)}</span>${esc(nodeTitle(node))}</button><span class="tree-indicators">${node.changed?'<span class="dot" title="自查看后有变化"></span>':''}${node.pending?'<span class="dot pending" title="待汇总"></span>':''}</span></div>${kids.length&&expanded?`<div role="group">${kids.map(child=>renderTree(child,depth+1,seen)).join('')}</div>`:''}</div>`;
}
function sidebar() {
  const tree = roots().map(root=>renderTree(root)).join('');
  return `${ui.mobile?'<div class="sidebar-backdrop" data-close-sidebar></div>':''}<aside class="sidebar${ui.mobile?' open':''}" aria-label="项目导航"><button class="brand" data-home aria-label="DocTree 项目总览"><span class="brand-mark">${icon('tree',25)}</span><span><span class="brand-name">Doc<span>Tree</span></span><div class="brand-tagline">A PLACE FOR EVERY IDEA</div></span></button><button class="nav-home${!ui.selected?' active':''}" data-home data-testid="home-link">${icon('home',16)}<span>项目总览</span><span class="nav-count">${projects().length}</span></button><div class="side-label"><span>WORKSPACE / 项目地图</span><span>${allNodes().length} 节点</span></div><select class="project-select" id="project-select" aria-label="选择项目"><option value="all">全部项目</option>${projects().map(project=>`<option value="${esc(project.id)}"${project.id===ui.project?' selected':''}>${esc(project.title || project.id)}</option>`).join('')}</select><div class="tree-scroll" role="tree" aria-label="README 项目树" data-testid="project-tree">${tree||'<p class="tree-empty">没有符合筛选的节点。<br>试试其他关键词，或清除筛选。</p>'}</div><div class="tree-footer"><span><i class="dot"></i> 有变化</span><span><i class="dot pending"></i> 待汇总</span></div><div class="sidebar-bottom"><div class="local-status"><i class="dot"></i><span>本地工作空间</span>${icon('shield',13)}</div><p>README 连接工作与证据<br>从一片叶子，看见整棵树</p><a class="tree-return" href="/tree">← 返回目录树</a></div></aside>`;
}
function topbar() { return `<header class="topbar"><button class="mobile-menu" data-menu aria-label="打开项目导航">${icon('menu',20)}</button><div class="topbar-label"><strong>我的工作空间</strong><span>/ &nbsp; 项目脉络，清晰可见</span></div><div class="topbar-right"><label class="search-wrap">${icon('search',14)}<input type="search" id="search" data-testid="search" aria-label="搜索节点" placeholder="搜索节点或文档…" value="${esc(ui.query)}" autocomplete="off"><span class="search-shortcut">/</span></label><a class="tree-return-top" data-testid="return-tree-top" href="/tree">${icon('tree',18)} 返回目录树 ${icon('arrow',15)}</a></div></header>`; }
function breadcrumb(node) { return `<nav class="breadcrumb" aria-label="面包屑"><button data-home>${icon('home',12)}</button>${node?ancestry(node).map((item,index,arr)=>`${icon('chevron',9)}${index===arr.length-1?`<span class="current">${esc(nodeTitle(item))}</span>`:`<button data-node="${esc(item.id)}">${esc(nodeTitle(item))}</button>`}`).join(''):`${icon('chevron',9)}<span class="current">项目总览</span>`}</nav>`; }
function actions(withContext = false) { return `<div class="actions">${withContext?`<button class="button" data-context="${esc(ui.selected)}" data-testid="export-context"${ui.busy?' disabled':''}>${icon('download',13)}导出上下文</button>`:''}<button class="button" data-action="refresh" data-testid="refresh"${ui.busy?' disabled':''}>${icon('refresh',13)}${ui.busy?'正在处理…':'刷新扫描'}</button><button class="button primary" data-action="rollup" data-testid="rollup"${ui.busy?' disabled':''}>${icon('layers',13)}生成待审阅汇总</button></div>`; }
function filters() { return `<div class="filters"><button class="filter-button${!ui.changed&&!ui.pending?' active':''}" data-filter="all" data-testid="filter-all">全部节点</button><button class="filter-button${ui.changed?' active':''}" data-filter="changed" data-testid="filter-changed">有变化 <span>${allNodes().filter(n=>n.changed).length}</span></button><button class="filter-button${ui.pending?' active':''}" data-filter="pending" data-testid="filter-pending">待汇总 <span>${allNodes().filter(n=>n.pending).length}</span></button></div>`; }
function drawing() { return '<svg class="intro-drawing" viewBox="0 0 150 118" fill="none" aria-hidden="true"><path d="M74 83V49M74 64H36V83M74 64H114V83" stroke="#a8ba87" stroke-width="1.5"/><rect x="54" y="17" width="40" height="31" rx="8" fill="#d4dfbf" stroke="#a8bd8e"/><rect x="18" y="83" width="36" height="27" rx="7" fill="#e3eacb" stroke="#b2c197"/><rect x="58" y="83" width="34" height="27" rx="7" fill="#ecf0e0" stroke="#b2c197"/><rect x="98" y="83" width="36" height="27" rx="7" fill="#c6d7a9" stroke="#9eb583"/><path d="M67 28h14m-14 7h10M29 94h14m-14 5h9m39-5h-4m-4 5h10m31-5h11m-11 5h7" stroke="#88a268" stroke-width="1.6" stroke-linecap="round"/><path d="M115 16c7-7 17-5 17-5s0 9-8 12c-8 3-9-1-9-1l-8 9" stroke="#a4b886" stroke-width="1.3"/><circle cx="22" cy="37" r="2" fill="#b7c49e"/><circle cx="137" cy="57" r="2" fill="#b7c49e"/></svg>'; }
function projectCard(project) { const root = projectRoot(project); const projectNodes = root ? descendants(root) : allNodes().filter(n=>n.project_id===project.id); const changed = projectNodes.filter(n=>n.changed).length; const pending = projectNodes.filter(n=>n.pending).length; return `<button class="project-card" data-node="${esc(root?.id||'')}" data-project="${esc(project.id)}" data-testid="project-card"><div class="project-card-top"><span class="project-symbol">${icon(root?.kind==='research'?'lab':'folder',21)}</span>${sourceBadge(project.source_type || root?.source_type)}</div><h3>${esc(project.title || nodeTitle(root))}</h3><p>${esc(root?.purpose || project.description || nodeSummary(root))}</p><div class="project-card-bottom"><span>${projectNodes.length} 个节点</span>${changed?`<span class="badge changed">${changed} 处变化</span>`:''}${pending?`<span class="badge pending">${pending} 项待汇总</span>`:''}${icon('arrow',14)}</div></button>`; }
function changeRow(node) { return `<button class="change-row" data-node="${esc(node.id)}"><span class="change-symbol">${icon(kindIcon(node),14)}</span><div class="change-info"><div class="change-title">${esc(nodeTitle(node))}${stateBadges(node)}${reviewBadge(node)}</div><p>${esc(nodeSummary(node))}</p></div>${icon('arrow',13)}</button>`; }
function empty(message, title = '这里暂时很安静') { return `<div class="empty-state">${icon('leaf',24)}<strong>${esc(title)}</strong>${esc(message)}</div>`; }
function overview() {
  let shown = allNodes().filter(matches);
  if(!activeFilters()) shown = shown.filter(n=>n.changed||n.pending).slice(0,8);
  const selectedProjects = projects().filter(project=>ui.project==='all'||project.id===ui.project);
  return `${breadcrumb()}<div class="page-heading"><div><span class="eyebrow">YOUR PROJECTS, CONNECTED</span><h1>让每一份进展，有迹可循。</h1><p>从项目到实验，逐层展开工作脉络。变化向上汇聚，结论回到证据。</p></div>${actions()}</div><div class="grid-content"><div><section class="overview-intro"><div><span class="eyebrow">GROW WITH CLARITY</span><h2>不止看见文件，也看见它们的联系。</h2><p>一棵树承载一个项目。沿着职责、子节点与原始记录，找到此刻值得关注的工作。</p></div>${drawing()}</section><section><div class="section-head"><h2>项目地图 <span class="count-text">${selectedProjects.length} 个项目</span></h2><p>逻辑组织 · 文件保持原位</p></div><div class="project-grid">${selectedProjects.map(projectCard).join('')}</div></section><section><div class="section-head"><h2>${activeFilters()?'筛选结果':'值得关注'} <span class="count-text">${shown.length}</span></h2>${filters()}</div>${ui.query?`<p class="search-result-note">包含 “${esc(ui.query)}” 的节点，左侧保留完整祖先路径。</p>`:''}${shown.length?`<div class="change-list">${shown.slice(0,80).map(changeRow).join('')}</div>`:empty(activeFilters()?'试试其他关键词，或清除筛选。':'当前没有变化或待汇总项。可以打开项目继续浏览。',activeFilters()?'没有符合条件的节点':'所有已知变化已展开')}<button class="button quiet small mt-11" data-action="viewed" data-testid="mark-viewed"${ui.busy?' disabled':''}>${icon('check',12)}将当前扫描标为已查看</button></section></div><aside class="right-panel">${overviewAside()}</aside></div>${footer()}`;
}
function overviewAside() { const total = allNodes(); const eventItems = eventsFor().slice(0,4); return `<section class="aside-card"><h2>${icon('layers',15)}工作空间概况</h2><div class="stat-line"><span>已接入项目</span><strong>${projects().length}</strong></div><div class="stat-line"><span>组织节点</span><strong>${total.length}</strong></div><div class="stat-line"><span>自查看后的变化</span><strong>${total.filter(n=>n.changed).length}</strong></div><div class="stat-line"><span>汇总流程待办</span><strong>${total.filter(n=>n.pending).length}</strong></div><div class="stat-line"><span>候选节点 · 待审阅</span><strong>${total.filter(n=>n.review!=='reviewed').length}</strong></div></section><section class="aside-card"><h2>${icon('clock',15)}最近记录</h2>${eventItems.length?`<div class="mini-timeline">${eventItems.map(event=>`<div class="mini-event"><time>${esc(dateText(event.timestamp||event.time||event.created_at,true))}</time><p>${esc(eventType(event))}${getNode(eventNodeId(event))?`<br><button data-node="${esc(eventNodeId(event))}">${esc(nodeTitle(getNode(eventNodeId(event))))}</button>`:''}</p></div>`).join('')}</div>`:'<p>还没有历史记录。刷新扫描或记录交付后，变化会留在这里。</p>'}</section><section class="aside-card note"><h2>${icon('leaf',15)}让摘要保留分寸</h2><p>扫描推断与自动汇总会保留“待审阅”标记。任务交付、代码集成、运行结果和最终验收各自记录，让证据说明进展。</p></section>`; }
function footer() { return `<footer class="footer-note"><span>DocTree · 本地 README 项目地图</span><span>扫描 ${esc(short(ui.data?.scan_id || ui.data?.scan_version || '尚未记录',18))} &nbsp; · &nbsp; ${esc(dateText(ui.data?.scanned_at || ui.data?.updated_at || ui.data?.timestamp || (ui.data?.scanned_at_ns ? ui.data.scanned_at_ns / 1e6 : null) || list(ui.data?.events).slice(-1)[0]?.time,true))}</span></footer>`; }

function stageValue(raw) { const value = textValue(raw?.status ?? raw ?? ''); const names={not_started:'未开始',not_run:'未运行',not_executed:'未运行',not_integrated:'未集成',not_accepted:'未验收',pending:'待处理',unverified:'待验证',unknown:'未记录',none:'未记录',done:'已完成',completed:'已完成',complete:'已完成',delivered:'已交付',implemented:'已实现',integrated:'已集成',merged:'已合并',passed:'已通过',accepted:'已验收',success:'执行成功',succeeded:'执行成功',failed:'失败',blocked:'阻塞',draft:'草稿',candidate:'候选',local_only:'仅本地',not_applicable:'不适用',not_reviewed:'未审阅',reviewed:'已审阅'}; return names[value] || value || '未记录'; }
function stages(node) { return `<div class="stage-context"><span>本节点状态</span><span>与子节点分别记录</span></div><div class="stage-strip" aria-label="本节点的四类独立状态">${[['delivery','任务交付','file'],['integration','代码集成','link'],['execution','执行结果','lab'],['acceptance','最终验收','shield']].map(([key,label,symbol])=>{const raw=node.stages?.[key];const value=raw==='pending'?({delivery:'待交付',integration:'待集成',execution:'待执行',acceptance:'待验收'}[key]):stageValue(raw);const cls=/未|待|候选|草稿/.test(value)?' unknown':/失败|阻塞/.test(value)?' error':''; return `<div class="stage-item"><div class="stage-label">${icon(symbol,11)}${label}</div><div class="stage-value${cls}" data-testid="stage-${key}">${esc(value)}</div></div>`;}).join('')}</div>`; }
function nodePage(node) {
  return `${breadcrumb(node)}<div class="page-heading"><div><span class="eyebrow">${esc(kindLabel(node.kind))} / PROJECT NODE</span><h1 data-testid="node-title">${esc(nodeTitle(node))}</h1><p>${esc(node.purpose || '这里记录此节点的职责、进展与证据。')}</p><div class="node-tags">${sourceBadge(node.source_type)}${reviewBadge(node)}${stateBadges(node)}<span class="node-id">${esc(node.id)}</span></div></div>${actions(true)}</div><div class="grid-content"><div><section class="summary-box" data-testid="node-summary"><div class="summary-box-head"><span>当前摘要</span>${node.summary_inputs?`<span class="badge ${node.review==='reviewed'?'reviewed':'candidate'}">${node.review==='reviewed'?'汇总已审阅':'汇总候选 · 待审阅'}</span>`:''}</div><p>${esc(nodeSummary(node))}</p>${summaryNotice(node)}</section>${stages(node)}<div class="tabs" role="tablist" aria-label="节点内容"><button class="tab${ui.tab==='overview'?' active':''}" role="tab" aria-selected="${ui.tab==='overview'}" data-tab="overview">节点概览</button><button class="tab${ui.tab==='readme'?' active':''}" role="tab" aria-selected="${ui.tab==='readme'}" data-tab="readme" data-testid="readme-tab">README 文档</button><button class="tab${ui.tab==='history'?' active':''}" role="tab" aria-selected="${ui.tab==='history'}" data-tab="history" data-testid="history-tab">变化记录 <span class="count-text">${eventsFor(node).length}</span></button></div><div role="tabpanel">${ui.tab==='history'?historyPanel(node):ui.tab==='readme'?readmePanel(node):nodeOverview(node)}</div></div><aside class="right-panel">${nodeAside(node)}</aside></div>${footer()}`;
}
function nodeOverview(node) { const kids=children(node); const related=list(node.related); return `${kids.length?`<section class="node-section"><div class="section-head"><h2>沿着分支继续 <span class="count-text">${kids.length}</span></h2><p>每个节点，一份明确职责</p></div><div class="children-list">${kids.map(child=>`<button class="child-card" data-node="${esc(child.id)}">${icon(kindIcon(child),18)}<div><strong>${esc(nodeTitle(child))}</strong><p>${esc(child.purpose || nodeSummary(child))}</p></div>${stateBadges(child)}${icon('chevron',12)}</button>`).join('')}</div></section>`:''}${related.length?`<section class="node-section"><h2>关联工作</h2><div class="children-list">${related.map(item=>{const target=getNode(item.id);return target?`<button class="child-card" data-node="${esc(target.id)}">${icon('link',17)}<div><strong>${esc(nodeTitle(target))}</strong><p>${esc(relationLabel(item.relation))} · ${esc(target.purpose||target.id)}</p></div>${icon('chevron',12)}</button>`:`<div class="child-card">${icon('link',17)}<div><strong>${esc(item.id)}</strong><p>${esc(relationLabel(item.relation))} · 关联目标尚未接入</p></div></div>`;}).join('')}</div></section>`:''}<section class="node-section"><div class="section-head"><h2>README 摘览</h2><button class="button quiet small" data-tab="readme">打开完整文档 ${icon('arrow',12)}</button></div><div class="document-card"><div class="markdown">${markdown(previewBody(node),node)}</div></div></section>`; }
function relationLabel(value) { return ({depends_on:'依赖',evidence_for:'支持依据',related_to:'相关工作',alternative_to:'替代方案',derived_from:'派生自',references:'参考'}[value]) || value || '关联'; }
function previewBody(node) { const body=stripProtocol(textValue(node.body || node.readme || node.content)); if(!body) return node.purpose || nodeSummary(node); const lines=body.split('\n'); return lines.length>38?lines.slice(0,38).join('\n')+'\n\n…\n\n打开完整文档，继续阅读。':body; }
function stripProtocol(body) { return body.replace(/(^|\n)(`{3,}|~{3,})[^\n]*\n([\s\S]*?)\n\2/g,(whole,start,fence,content)=>/\bproject_node\s*["']?\s*:/.test(content)?start:whole); }
function readmePanel(node) { return `<section class="document-card" data-testid="readme-content"><div class="document-header"><span>${icon('file',12)} ${esc(node.entry || 'README')}</span>${node.entry?`<button class="button quiet small" data-source="${esc(node.entry)}" data-source-node="${esc(node.id)}">查看原始文件 ${icon('arrow',11)}</button>`:''}</div><article class="markdown">${markdown(textValue(node.body || node.readme || node.content) || '此节点尚无正文。请从右侧证据入口查看来源。',node)}</article></section>`; }
function combinedFlags(node) { const direct=node.flags||{}; const aggregate=node.aggregate_flags||{}; const result={}; ['blocked','unverified','decisions'].forEach(key=>result[key]=Array.from(new Set([...list(direct[key]),...list(aggregate[key])].map(textValue).filter(Boolean)))); return result; }
function flagItem(node,key,value) { const sources=list(node.flag_sources?.[key]).filter(item=>item.text===value&&item.node_id!==node.id); const ids=Array.from(new Set(sources.map(item=>item.node_id))); return `<li>${esc(value)}${ids.map(id=>`<button class="flag-origin" data-node="${esc(id)}">来自 ${esc(nodeTitle(getNode(id)||{id}))} ${icon('arrow',9)}</button>`).join('')}</li>`; }
function eventEvidence(event) { const nodeId=eventNodeId(event); if(!getNode(nodeId))return ''; return list(event.evidence).filter(item=>item?.path).map(item=>`<button class="evidence-link history-evidence" data-source="${esc(item.path)}" data-source-node="${esc(nodeId)}">${icon('file',12)}<div><strong>${esc(item.label||item.path)}</strong><small>交付时证据${item.sha256?' · SHA256 '+esc(short(item.sha256,16)):''}</small></div></button>`).join(''); }
function nodeAside(node) {
  const flags=combinedFlags(node); const flagsExist=Object.values(flags).some(values=>values.length); const evidence=list(node.evidence); const constraints=list(node.constraints).map(textValue).filter(Boolean); const rollup=node.summary_inputs;
  return `<section class="aside-card"><h2>${icon('flag',15)}需要留意</h2>${flagsExist?Object.entries(flags).filter(([,values])=>values.length).map(([key,values])=>`<div class="flag-group"><div class="flag-label ${key}">${key==='blocked'?'阻塞':key==='unverified'?'待验证':'待决策'} <span>${values.length}</span></div><ul>${values.map(value=>flagItem(node,key,value)).join('')}</ul></div>`).join(''):'<p>当前未登记阻塞、待验证或待决策事项。</p>'}</section><section class="aside-card"><h2>${icon('file',15)}证据入口 <span class="count-text">${evidence.length}</span></h2>${evidence.length?`<div class="evidence-list">${evidence.map(item=>{const path=typeof item==='string'?item:item.path;return `<button class="evidence-link" data-source="${esc(path||'')}" data-source-node="${esc(node.id)}" data-testid="evidence-link">${icon('file',13)}<div><strong>${esc(item.label||path||'未提供路径')}</strong><small>${esc(path||'')}${item.sha256?`<br>SHA256 ${esc(short(item.sha256,14))}`:''}</small></div></button>`;}).join('')}</div>`:(node.summary_inputs&&children(node).length?'<p>本节点未另附直接证据。上级汇总依据见下方输入溯源，也可展开子节点查看原始记录。</p>':'<p>尚未列出直接证据。摘要需要能回到可检查的来源。</p>')}</section>${constraints.length?`<section class="aside-card note"><h2>${icon('shield',15)}工作约束</h2><ul class="constraint-list">${constraints.map(value=>`<li>${esc(value)}</li>`).join('')}</ul></section>`:''}<section class="aside-card"><h2>${icon('layers',15)}来源与汇总依据</h2><div class="provenance"><div class="version-row"><span>节点版本</span><code title="${esc(node.revision||node.version||'')}">${esc(short(node.revision||node.version||'未记录',18))}</code></div><div class="version-row"><span>扫描版本</span><code>${esc(short(ui.data.scan_id||'未记录',18))}</code></div><div class="version-row"><span>摘要覆盖状态</span><span>${summaryCoverage(node)}</span></div>${rollup?`<div class="version-row"><span>汇总审阅状态</span><span>${node.review==='reviewed'?'已审阅':'候选 · 待审阅'}</span></div>${node.reviewer?`<div class="version-row"><span>审阅记录人</span><span>${esc(node.reviewer)}</span></div>`:''}<details data-testid="rollup-provenance"><summary>查看输入版本与溯源</summary>${Object.entries(rollup.input_versions||{}).map(([id,version])=>`<div class="input-source"><button class="text-link" data-node="${esc(id)}">${esc(nodeTitle(getNode(id)||{id}))}</button><code>${esc(short(version,16))}</code></div>`).join('')}<pre>${esc(JSON.stringify(rollup,null,2))}</pre></details>`:'<p class="mt-11">尚未生成逐级汇总。当前内容来自节点记录或扫描候选。</p>'}</div><button class="button full small mt-16" data-context="${esc(node.id)}">${icon('download',12)}导出此节点上下文包</button></section>`;
}
function historyPanel(node) { const items=eventsFor(node); return items.length?`<section class="document-card"><ul class="history-list">${items.slice(0,100).map(event=>`<li class="history-item"><h3>${esc(eventType(event))}</h3><time>${esc(dateText(event.timestamp||event.time||event.created_at))}</time>${eventNodeId(event)?`<p><button class="text-link" data-node="${esc(eventNodeId(event))}">${esc(nodeTitle(getNode(eventNodeId(event))||{id:eventNodeId(event)}))} ${icon('arrow',11)}</button></p>`:''}${event.summary||event.message?`<p>${esc(textValue(event.summary||event.message))}</p>`:''}${eventEvidence(event)}<details><summary>查看记录详情</summary><pre>${esc(JSON.stringify(event,null,2))}</pre></details></li>`).join('')}</ul></section>`:empty('该节点及其分支尚无历史事件。后续扫描、交付和汇总会记录在这里。','每一次变化，都可以追溯'); }

// A deliberately small Markdown reader. No HTML passthrough, embeds or script URLs.
function inline(raw,node) {
  const pattern=/(`[^`\n]+`|!\[[^\]\n]*\]\([^\)\n]+\)|\[[^\]\n]+\]\([^\)\n]+\)|\*\*[^*\n]+\*\*|__[^_\n]+__|\*[^*\n]+\*)/g;
  let result='',offset=0,match;
  while((match=pattern.exec(raw))!==null) { result+=esc(raw.slice(offset,match.index)); const token=match[0];
    if(token.startsWith('`')) result+=`<code>${esc(token.slice(1,-1))}</code>`;
    else if(token.startsWith('![')) result+=`<span class="muted">[图片：${esc(token.slice(2,token.indexOf(']')))}]</span>`;
    else if(token.startsWith('[')) { const index=token.indexOf(']('); const label=token.slice(1,index); const target=token.slice(index+2,-1).trim().replace(/\s+["'][^"']*["']$/,'');
      if(/^https?:\/\//i.test(target)) result+=`<a href="${esc(target)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>`;
      else if(/^[a-z][a-z0-9+.-]*:/i.test(target)||target.startsWith('//')) result+=esc(label);
      else if(target.startsWith('#')) result+=`<span>${esc(label)}</span>`;
      else result+=`<a href="#" data-markdown-source="${esc(target)}" data-source-node="${esc(node.id)}">${esc(label)}</a>`;
    } else if(token.startsWith('**')||token.startsWith('__')) result+=`<strong>${esc(token.slice(2,-2))}</strong>`;
    else result+=`<em>${esc(token.slice(1,-1))}</em>`;
    offset=match.index+token.length;
  }
  return result+esc(raw.slice(offset));
}
function markdown(source,node) {
  source=String(source||'').replace(/^<!-- doctree:(?:node\s+[^\n]*|purpose:(?:start|end)|nav:(?:start|end)) -->\s*$/gm,'');
  const lines=String(source||'').replace(/\r\n?/g,'\n').split('\n'); let out='',i=0;
  const special=line=>/^\s*$|^#{1,6}\s|^\s*(```|~~~)|^\s*[-*+]\s+|^\s*\d+\.\s+|^\s*>|^\s*([-*_])\1{2,}\s*$/.test(line);
  const cells=line=>line.trim().replace(/^\|/,'').replace(/\|$/,'').split('|').map(cell=>cell.trim());
  while(i<lines.length) {
    const line=lines[i];
    if(!line.trim()) {i++;continue;}
    const fence=line.match(/^\s*(```+|~~~+)\s*(.*)$/);
    if(fence) {const body=[];i++;while(i<lines.length&&!lines[i].trim().startsWith(fence[1]))body.push(lines[i++]);if(i<lines.length)i++;const content=body.join('\n');const code=`<pre><code>${esc(content)}</code></pre>`;out+=/\bproject_node\s*["']?\s*:/.test(content)?`<details class="protocol-details"><summary>节点协议元数据 <span>供维护与核对</span></summary>${code}</details>`:code;continue;}
    const heading=line.match(/^(#{1,6})\s+(.+)$/);
    if(heading) {out+=`<h${heading[1].length}>${inline(heading[2],node)}</h${heading[1].length}>`;i++;continue;}
    if(/^\s*([-*_])\1{2,}\s*$/.test(line)) {out+='<hr>';i++;continue;}
    if(line.includes('|')&&i+1<lines.length&&/^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[i+1])) {
      const headers=cells(line);i+=2;const rows=[];while(i<lines.length&&lines[i].trim()&&lines[i].includes('|'))rows.push(cells(lines[i++]));out+=`<div class="table-wrap"><table><thead><tr>${headers.map(cell=>`<th>${inline(cell,node)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${headers.map((_,index)=>`<td>${inline(row[index]||'',node)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;continue;
    }
    if(/^\s*>/.test(line)) {const quote=[];while(i<lines.length&&/^\s*>/.test(lines[i]))quote.push(lines[i++].replace(/^\s*>\s?/,''));out+=`<blockquote>${markdown(quote.join('\n'),node)}</blockquote>`;continue;}
    if(/^\s*([-*+]\s+|\d+\.\s+)/.test(line)) {const ordered=/^\s*\d+\./.test(line),items=[];const type=ordered?'ol':'ul';const start=ordered?Number(line.match(/^\s*(\d+)\./)[1]):null;while(i<lines.length&&(ordered?/^\s*\d+\.\s+/:/^\s*[-*+]\s+/).test(lines[i])){const item=lines[i++].replace(/^\s*([-*+]\s+|\d+\.\s+)/,'');items.push(item.replace(/^\[ \]/,'☐').replace(/^\[[xX]\]/,'☑'));}out+=`<${type}${ordered?` start="${start}"`:''}>${items.map(item=>`<li>${inline(item,node)}</li>`).join('')}</${type}>`;continue;}
    const paragraph=[line];i++;while(i<lines.length&&!special(lines[i])&&!(lines[i].includes('|')&&lines[i+1]?.match(/[-]{3,}/)))paragraph.push(lines[i++]);out+=`<p>${paragraph.map(item=>inline(item,node)).join('<br>')}</p>`;
  }
  return out;
}
function relativePath(entry,target) { const stripped=target.split('#')[0].split('?')[0]; let decoded; try {decoded=decodeURIComponent(stripped);} catch(_){decoded=stripped;} if(decoded.startsWith('/')) return decoded.slice(1); const parts=String(entry||'').replace(/\\/g,'/').split('/').slice(0,-1); for(const part of decoded.replace(/\\/g,'/').split('/')) {if(!part||part==='.')continue;if(part==='..')parts.pop();else parts.push(part);} return parts.join('/'); }
async function openSource(nodeId,path) { const node=getNode(nodeId); if(!node||!path)return; const dialog=$('#source-dialog'); const request=++ui.sourceRequest; $('#source-title').textContent=path.split(/[\\/]/).pop()||path; $('#source-meta').textContent=path;$('#source-meta').classList.remove('source-stale'); $('#source-body').innerHTML='<p class="inline-empty">正在读取原始依据…</p>'; if(!dialog.open)dialog.showModal(); try {const data=await api(`/api/source?node=${encodeURIComponent(nodeId)}&path=${encodeURIComponent(path)}`);if(request!==ui.sourceRequest)return;$('#source-meta').textContent=`${data.path||path}${data.sha256?' · SHA256 '+data.sha256:''}`;$('#source-meta').classList.toggle('source-stale',!!data.stale);$('#source-body').innerHTML=(data.stale?'<div class="error-banner">此文件已在上次扫描后变化。以下为当前文件；刷新扫描后再将其作为汇总依据。</div>':'')+(/\.(md|markdown)$/i.test(data.path||path)?`<article class="markdown">${markdown(data.content||'',{...node,entry:data.path||path})}</article>`:`<pre>${esc(data.content||'')}</pre>`);}catch(error){if(request===ui.sourceRequest)$('#source-body').innerHTML=`<div class="error-banner">${esc(error.message)}</div>`;} }
async function exportContext(nodeId) { try {const data=await api(`/api/context?node=${encodeURIComponent(nodeId)}`);const blob=new Blob([JSON.stringify(data,null,2)+'\n'],{type:'application/json;charset=utf-8'});const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download=`${nodeId.replace(/[^\w.-]/g,'_')}.context.json`;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);showToast('上下文包已导出，包含节点、约束与来源依据。');}catch(error){showToast(error.message,true);} }
function selectNode(id) { if(!getNode(id))return;ui.selected=id;ui.tab='overview';ui.mobile=false;ancestry(getNode(id)).forEach(node=>ui.expanded.add(node.id));try{localStorage.setItem('doctree.node',id);}catch(_){}render();window.scrollTo({top:0,behavior:'instant'}); }
function goHome() {ui.selected=null;ui.tab='overview';ui.mobile=false;try{localStorage.removeItem('doctree.node');}catch(_){}render();window.scrollTo({top:0,behavior:'instant'});}
function render() { clearTimeout(ui.searchRenderTimer); ui.searchRenderTimer=null; const node=getNode(ui.selected);$('#app').setAttribute('aria-busy',String(ui.busy));$('#app').innerHTML=`<div class="app-layout">${sidebar()}<div class="workspace">${topbar()}<main class="main">${ui.error?`<div class="error-banner" role="alert"><span>${esc(ui.error)}</span><button class="button small" data-action="retry">重试</button></div>`:''}${node?nodePage(node):overview()}</main></div>`;document.title=`${node?nodeTitle(node)+' · ':''}DocTree`; }

document.addEventListener('click',event=>{
  const target=event.target.closest('button,a,[data-close-sidebar]');if(!target)return;
  if(target.hasAttribute('data-home'))return goHome();
  if(target.hasAttribute('data-node')&&target.dataset.node)return selectNode(target.dataset.node);
  if(target.hasAttribute('data-expand')){const id=target.dataset.expand;ui.expanded.has(id)?ui.expanded.delete(id):ui.expanded.add(id);return render();}
  if(target.hasAttribute('data-tab')){ui.tab=target.dataset.tab;return render();}
  if(target.hasAttribute('data-menu')){ui.mobile=!ui.mobile;return render();}
  if(target.hasAttribute('data-close-sidebar')){ui.mobile=false;return render();}
  if(target.hasAttribute('data-filter')){const filter=target.dataset.filter;if(filter==='all'){ui.changed=false;ui.pending=false;}else if(filter==='changed')ui.changed=!ui.changed;else ui.pending=!ui.pending;return render();}
  if(target.hasAttribute('data-context'))return exportContext(target.dataset.context);
  if(target.hasAttribute('data-source'))return openSource(target.dataset.sourceNode,target.dataset.source);
  if(target.hasAttribute('data-markdown-source')){event.preventDefault();const node=getNode(target.dataset.sourceNode);const base=$('#source-dialog').open?$('#source-meta').textContent.split(' · SHA256 ')[0]:node?.entry;return openSource(node?.id,relativePath(base,target.dataset.markdownSource));}
  if(target.hasAttribute('data-action')){const name=target.dataset.action;if(name==='refresh')return action('/api/refresh','扫描已刷新；新的变化与受影响分支已更新。');if(name==='rollup')return action('/api/rollup','逐级汇总已处理。生成内容保留待审阅状态。');if(name==='viewed')return action('/api/viewed','已记录当前查看基线，后续变化会重新标记。');if(name==='retry')return load();}
});
document.addEventListener('change',event=>{if(event.target.id==='project-select'){ui.project=event.target.value;ui.selected=null;render();}});
document.addEventListener('input',event=>{
  if(event.target.id!=='search')return;
  // Save the value before any navigation can render. Only presentation is delayed.
  ui.query=event.target.value;
  clearTimeout(ui.searchRenderTimer);
  if(event.isComposing)return;
  ui.searchRenderTimer=setTimeout(()=>{
    const previous=$('#search');
    const focused=document.activeElement===previous;
    const start=previous?.selectionStart;
    render();
    if(focused){const input=$('#search');input.focus();try{input.setSelectionRange(start,start);}catch(_){}}
  },120);
});
document.addEventListener('keydown',event=>{if(event.key==='/'&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName)&&!$('#source-dialog').open){event.preventDefault();$('#search')?.focus();}if(event.key==='Escape'&&ui.mobile){ui.mobile=false;render();}});
$('#close-dialog').addEventListener('click',()=>$('#source-dialog').close());
$('#source-dialog').addEventListener('click',event=>{if(event.target===$('#source-dialog')){const rect=event.target.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)event.target.close();}});
async function load() { try {await refreshState();render();}catch(error){ui.error=error.message;ui.data=ui.data||{nodes:{},projects:[],events:[]};render();} }
load();

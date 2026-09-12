/* A local, dependency-free directory viewer. Source text is data, never executable HTML. */
'use strict';
const $ = (selector, root = document) => root.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const SVG_NS = 'http://www.w3.org/2000/svg';
const CARD = {width:272, height:86, column:360, gap:18};
const view = {data:null, project:null, selected:null, expanded:new Set(), positions:new Map(), shown:[], zoom:1, x:60, y:80, bounds:null, scope:'all', displayDepth:1, managedBranches:new Set(), query:'', matches:[], matchIndex:-1, missingIndex:-1, sourceRequest:0, sourcePath:null, drag:null, moving:false, suppressClick:false};

function svgElement(tag, attrs = {}, children = []) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [key,value] of Object.entries(attrs)) if(value !== null && value !== undefined) el.setAttribute(key, String(value));
  for (const child of children) el.append(typeof child === 'string' ? document.createTextNode(child) : child);
  return el;
}
function node(id) { return view.data?.nodes?.[id]; }
function project() { return view.data?.projects?.find(item => item.id === view.project); }
function childNodes(item) { return (item?.children || []).map(node).filter(Boolean); }
function visibleChildren(item) { return childNodes(item).filter(child => view.scope !== 'managed' || view.managedBranches.has(child.id)); }
function projectNodes() { return Object.values(view.data?.nodes || {}).filter(item => item.project_id === view.project); }
function hasReadme(item) { return typeof item?.has_readme === 'boolean' ? item.has_readme : /(?:^|\/)readme\.md$/i.test(item?.entry || ''); }
function missingReadmes() { return projectNodes().filter(item => !hasReadme(item)); }
function updateCoverage() {
  const missing = missingReadmes(), button = $('#missing-readmes');
  button.textContent = missing.length ? `尚无 README ${missing.length}  ↗` : '已扫描目录均有 README';
  button.disabled = !missing.length; button.classList.toggle('complete',!missing.length);
  button.title = `已扫描目录中，${missing.length} 个尚无 README，可按需要接入。点击逐个定位；页面不会创建说明文件。`;
  $('#readme-coverage').textContent = `已扫描目录中 ${missing.length} 个尚无 README，可按需接入`;
}
function nodeTitle(item) { return String(item?.title || item?.directory || '未命名目录'); }
function purpose(item) { return item?.managed ? String(item.purpose || '尚未填写目录职责') : '尚未添加目录说明'; }
function ancestors(item) {
  const result = [], visited = new Set();
  while(item && !visited.has(item.id)) { result.unshift(item); visited.add(item.id); item = node(item.parent); }
  return result;
}
function shorten(value, maxWidth) {
  let width = 0, result = '';
  for(const char of String(value)) { width += char.charCodeAt(0) > 255 ? 2 : .98; if(width > maxWidth) return result + '…'; result += char; }
  return result;
}
function message(text, error = false) {
  const el = $('#toast'); el.textContent = text; el.className = `toast visible${error ? ' error' : ''}`;
  clearTimeout(message.timer); message.timer = setTimeout(() => el.classList.remove('visible'), 4700);
}
async function api(path, options = {}) {
  const response = await fetch(path, {headers:{'Content-Type':'application/json'}, ...options});
  const data = await response.json().catch(() => ({error:'无法读取本地服务的响应。'}));
  if(!response.ok || data.error) throw new Error(String(data.error || `读取失败（${response.status}）`));
  return data;
}
function showCanvasMessage(title, description, error = false) {
  const el = $('#canvas-message'); el.hidden = false; el.classList.toggle('error', error);
  $('h2',el).textContent = title; $('p',el).textContent = description;
}
function selectProject(id, initial = false) {
  view.project = id; view.selected = null; view.expanded.clear(); view.query = ''; view.matches = []; view.matchIndex = -1; view.missingIndex = -1;
  $('#tree-search').value = ''; $('#search-results').textContent = ''; $('#project-select').value = id;
  $('#node-drawer').hidden = true; view.sourceRequest++;
  const root = node(project()?.root_id);
  if(root) view.expanded.add(root.id);
  $('#canvas-project').textContent = project()?.title || id;
  const items = projectNodes();
  view.managedBranches = new Set(items.filter(item => item.managed).flatMap(item => ancestors(item).map(parent => parent.id)));
  view.scope = 'all';
  view.displayDepth = 1; $('#tree-depth').value = '1';
  updateScopeButtons();
  $('#tree-stats').textContent = `${items.length} 个目录 · ${items.filter(item => item.managed).length} 已接入`;
  updateCoverage();
  renderTree();
  requestAnimationFrame(() => fitTree(.9));
  try { localStorage.setItem('doctree.tree.project', id); } catch (_) { /* Optional local preference. */ }
}
function updateScopeButtons() {
  $('#scope-managed').setAttribute('aria-pressed',String(view.scope === 'managed'));
  $('#scope-all').setAttribute('aria-pressed',String(view.scope === 'all'));
  $('#scope-managed').disabled = view.managedBranches.size === 0;
}
function changeScope(scope) {
  view.scope = scope;
  updateScopeButtons(); setExpansionDepth(view.displayDepth === 'custom' ? 1 : view.displayDepth);
}
function markManualExpansion() { view.displayDepth = 'custom'; $('#tree-depth').value = 'custom'; }
function expandAncestors(item) {
  let changed = false;
  for(const parent of ancestors(item).slice(0,-1)) { if(!view.expanded.has(parent.id)) changed = true; view.expanded.add(parent.id); }
  if(changed) markManualExpansion();
}
function setExpansionDepth(depth) {
  const limit = Math.max(1,Math.min(3,Number(depth) || 1));
  view.displayDepth = limit; $('#tree-depth').value = String(limit); view.expanded.clear();
  const seen = new Set();
  function visit(item, level) {
    if(!item || seen.has(item.id) || level >= limit) return;
    seen.add(item.id); view.expanded.add(item.id);
    for(const child of visibleChildren(item)) visit(child,level+1);
  }
  visit(node(project()?.root_id),0); renderTree(); fitTree(.9);
}
function buildLayout() {
  const positions = new Map(), shown = [], visited = new Set(); let nextY = 0, maxDepth = 0;
  function visit(item, depth) {
    if(!item || visited.has(item.id)) return null;
    visited.add(item.id); maxDepth = Math.max(maxDepth,depth);
    const children = view.expanded.has(item.id) ? visibleChildren(item) : [];
    const places = children.map(child => visit(child,depth + 1)).filter(Boolean);
    let y;
    if(places.length) y = (places[0].y + places[places.length - 1].y) / 2;
    else { y = nextY; nextY += CARD.height + CARD.gap; }
    const position = {x:depth * CARD.column, y, depth, node:item};
    positions.set(item.id,position); shown.push(item); return position;
  }
  visit(node(project()?.root_id), 0);
  view.positions = positions; view.shown = shown;
  view.bounds = shown.length ? {width:maxDepth * CARD.column + CARD.width + 27, height:Math.max(CARD.height,nextY - CARD.gap)} : null;
}
function renderTree() {
  buildLayout();
  const world = $('#tree-world'); world.replaceChildren();
  if(!view.shown.length) {
    showCanvasMessage('这个项目还没有目录入口', '检查项目配置与目录权限后，点击刷新。'); return;
  }
  $('#canvas-message').hidden = true;
  const edges = svgElement('g', {'aria-hidden':'true'}), cards = svgElement('g');
  for(const item of view.shown) {
    const current = view.positions.get(item.id), parent = view.positions.get(item.parent);
    if(parent) {
      const x1 = parent.x + CARD.width, y1 = parent.y + CARD.height/2, x2 = current.x, y2 = current.y + CARD.height/2;
      edges.append(svgElement('path',{d:`M ${x1} ${y1} C ${x1 + 46} ${y1}, ${x2 - 46} ${y2}, ${x2} ${y2}`, class:`tree-edge${item.managed && parent.node.managed ? ' managed-edge' : ''}`}));
    }
    cards.append(renderCard(item,current));
  }
  world.append(edges,cards); applyTransform();
  $('#visible-stats').textContent = `${view.scope === 'managed' ? '已接入分支' : '全部目录'} · 当前展开 ${view.shown.length} / ${projectNodes().length} 个目录`;
}
function renderCard(item, position) {
  const kids = visibleChildren(item), expanded = view.expanded.has(item.id), managed = !!item.managed;
  const missingReadme = !hasReadme(item);
  const root = !item.parent || item.id === project()?.root_id;
  const group = svgElement('g',{transform:`translate(${position.x} ${position.y})`});
  const card = svgElement('g', {class:`folder-node ${managed ? 'managed' : 'unmanaged'}${missingReadme?' missing-readme':''}${root?' root':''}${view.selected === item.id?' selected':''}${view.matches.some(match => match.id === item.id)?' match':''}`,role:'button',tabindex:'0','aria-label':`${nodeTitle(item)}，${managed?'已接入':'未接入'}${missingReadme?'，尚无 README':''}，${purpose(item)}`,'aria-pressed':view.selected === item.id,'data-testid':'tree-node','data-node-id':item.id});
  card.append(svgElement('title',{},[`${nodeTitle(item)}\n${item.directory || '.'}\n${purpose(item)}`]));
  card.append(svgElement('rect',{class:'node-background',x:0,y:0,width:CARD.width,height:CARD.height,rx:12}));
  card.append(svgElement('path',{class:'node-folder-icon',d:'M17 25v-8a2 2 0 0 1 2-2h5l3 3h8a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H19a2 2 0 0 1-2-2Z'}));
  card.append(svgElement('text',{class:'node-title',x:46,y:30},[shorten(nodeTitle(item),28)]));
  card.append(svgElement('text',{class:'node-purpose',x:18,y:50},[shorten(purpose(item),43)]));
  const badgeText = missingReadme ? '尚无 README' : managed ? '已接入' : '有 README';
  const badgeWidth = missingReadme ? 74 : managed ? 45 : hasReadme(item) ? 66 : 45;
  const badgeClass = missingReadme ? ' missing' : managed ? '' : ' unmanaged';
  card.append(svgElement('rect',{class:`node-badge-bg${badgeClass}`,x:18,y:61,width:badgeWidth,height:15,rx:4}));
  card.append(svgElement('text',{class:`node-badge${badgeClass}`,x:18 + badgeWidth/2,y:72,'text-anchor':'middle'},[badgeText]));
  const totalKids = childNodes(item).length;
  const count = item.collapsed_reason ? '已汇总 · 未展开内部' : view.scope === 'managed' && totalKids > kids.length ? `${kids.length} / ${totalKids} 个子目录` : `${kids.length} 个子目录`;
  card.append(svgElement('text',{class:'node-count',x:CARD.width - 16,y:72,'text-anchor':'end'},[count]));
  card.addEventListener('click',event => { event.stopPropagation(); if(!view.suppressClick) selectNode(item.id); });
  card.addEventListener('keydown',event => { if(event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectNode(item.id); } else if(event.key === 'ArrowRight' && kids.length && !expanded) { event.preventDefault(); toggleNode(item.id); } else if(event.key === 'ArrowLeft' && expanded) { event.preventDefault(); toggleNode(item.id); } });
  group.append(card);
  if(kids.length) {
    const toggle = svgElement('g',{class:'expand-control',transform:`translate(${CARD.width + 15} ${CARD.height/2})`,role:'button',tabindex:'0','data-testid':'tree-expand','data-expand-id':item.id,'aria-expanded':expanded,'aria-label':`${expanded?'折叠':'展开'} ${nodeTitle(item)} 的 ${kids.length} 个子目录`});
    toggle.append(svgElement('circle',{cx:0,cy:0,r:10}));
    toggle.append(svgElement('text',{x:0,y:4,'text-anchor':'middle'},[expanded?'−':'+']));
    if(!expanded) toggle.append(svgElement('text',{class:'expand-count',x:0,y:24,'text-anchor':'middle'},[String(kids.length)]));
    toggle.addEventListener('click', event => { event.stopPropagation(); if(!view.suppressClick) toggleNode(item.id); });
    toggle.addEventListener('keydown', event => { if(event.key === 'Enter' || event.key === ' ') { event.preventDefault(); toggleNode(item.id); } });
    group.append(toggle);
  }
  return group;
}
function toggleNode(id) {
  const previous = view.positions.get(id);
  if(view.expanded.has(id)) view.expanded.delete(id); else view.expanded.add(id);
  markManualExpansion();
  renderTree();
  const current = view.positions.get(id);
  if(previous && current) { view.y += (previous.y - current.y) * view.zoom; applyTransform(); }
}
function applyTransform() {
  $('#tree-world').setAttribute('transform',`translate(${view.x} ${view.y}) scale(${view.zoom})`);
  $('#zoom-level').textContent = `${Math.round(view.zoom*100)}%`;
}
function fitTree(minimum = .25) {
  if(!view.bounds) return;
  const rect = $('#canvas-wrap').getBoundingClientRect();
  if(!rect.width || !rect.height) return;
  view.zoom = Math.max(minimum,Math.min(1.12,(rect.width - 100)/view.bounds.width,(rect.height - 135)/view.bounds.height));
  view.x = Math.max(36,(rect.width - view.bounds.width*view.zoom)/2);
  const scaledHeight = view.bounds.height*view.zoom;
  view.y = scaledHeight > rect.height - 120 ? 40 : (rect.height - scaledHeight)/2 - 8;
  if(scaledHeight > rect.height - 120) {
    const rootPosition = view.positions.get(project()?.root_id);
    if(rootPosition) view.y = Math.min(view.y,rect.height - 16 - (rootPosition.y + CARD.height)*view.zoom);
  }
  applyTransform();
}
function zoomBy(factor, clientX, clientY) {
  const rect = $('#tree-canvas').getBoundingClientRect();
  const cx = clientX === undefined ? rect.width/2 : clientX - rect.left, cy = clientY === undefined ? rect.height/2 : clientY - rect.top;
  const next = Math.max(.2,Math.min(2.2,view.zoom*factor));
  const ratio = next/view.zoom; view.x = cx - (cx-view.x)*ratio; view.y = cy - (cy-view.y)*ratio; view.zoom = next; applyTransform();
}
function focusNode(id) {
  const p = view.positions.get(id), rect = $('#canvas-wrap').getBoundingClientRect();
  if(!p) return;
  view.zoom = Math.max(.85,view.zoom);
  view.x = rect.width/2 - (p.x + CARD.width/2)*view.zoom;
  view.y = rect.height/2 - (p.y + CARD.height/2)*view.zoom;
  applyTransform();
}
function localDetails(item) {
  if(item.detail_node_id) return `/details?node=${encodeURIComponent(item.detail_node_id)}`;
  return `/details?project=${encodeURIComponent(item.project_id)}${item.entry ? `&entry=${encodeURIComponent(item.entry)}` : ''}`;
}
function selectNode(id, center = false) {
  const item = node(id); if(!item) return;
  if(item.project_id !== view.project) selectProject(item.project_id);
  if(view.scope === 'managed' && !view.managedBranches.has(id)) { view.scope = 'all'; view.expanded.clear(); updateScopeButtons(); }
  view.selected = id; expandAncestors(item);
  $('#node-drawer').hidden = false;
  $('#drawer-badge').replaceChildren();
  const badge = document.createElement('span'); badge.className = `drawer-badge${item.managed?'':' unmanaged'}`; badge.textContent = item.managed ? '已接入目录说明' : '未接入'; $('#drawer-badge').append(badge);
  $('#drawer-title').textContent = nodeTitle(item);
  $('#drawer-directory').textContent = item.directory === '.' ? '项目根目录 /' : item.directory || '/';
  $('#drawer-purpose').textContent = purpose(item);
  const missing = !hasReadme(item);
  $('#drawer-readme-notice').hidden = !missing;
  $('#drawer-readme-notice').textContent = missing ? '本目录尚无 README.md，可按需要接入。页面展开目录不会创建说明文件。' : '';
  $('#node-details').href = localDetails(item);
  $('#open-folder').disabled = false;
  renderNavigation(item); renderTree();
  const position = view.positions.get(id), rect = $('#canvas-wrap').getBoundingClientRect();
  const outside = position && (view.x+position.x*view.zoom < 0 || view.x+(position.x+CARD.width)*view.zoom > rect.width || view.y+position.y*view.zoom < 0 || view.y+(position.y+CARD.height)*view.zoom > rect.height);
  if(center || outside) requestAnimationFrame(() => focusNode(id));
  loadSource(item.entry);
}
function navigationButton(item) {
  const button = document.createElement('button'); button.type = 'button'; button.className = 'node-link'; button.textContent = nodeTitle(item); button.addEventListener('click',() => selectNode(item.id,true)); return button;
}
function renderNavigation(item) {
  const container = $('#drawer-navigation'); container.replaceChildren();
  const parent = node(item.parent), children = childNodes(item);
  for(const [label,items] of [['上级',parent?[parent]:[]],['子目录',children]]) {
    const row = document.createElement('div'); row.className = 'navigation-row';
    const title = document.createElement('span'); title.textContent = label;
    const links = document.createElement('div'); links.className = 'navigation-links';
    if(items.length) for(const child of items) links.append(navigationButton(child));
    else links.textContent = label === '上级' ? '项目根目录' : item.collapsed_reason ? '内部目录未展开' : '暂无子目录';
    row.append(title,links); container.append(row);
  }
  if(item.collapsed_reason) { const note = document.createElement('p'); note.className = 'node-children-note'; note.textContent = item.collapsed_reason; container.append(note); }
}

/* Link resolution stays within the selected project. HTTP links open only on an explicit click. */
function resolveLink(raw, sourcePath) {
  let target = String(raw || '').trim();
  if(!target || /[\u0000-\u0020\u007f\uE000\uE001]/.test(target)) return null;
  if(/^https?:\/\//i.test(target)) { try { const url = new URL(target); return ['http:','https:'].includes(url.protocol) ? {external:url.href} : null; } catch (_) { return null; } }
  if(/^[a-z][a-z0-9+.-]*:/i.test(target) || target.startsWith('//') || target.startsWith('\\')) return null;
  let decoded;
  try { decoded = decodeURIComponent(target.split('#')[0].split('?')[0]); } catch (_) { return null; }
  if(!decoded) return {path:sourcePath};
  if(decoded.startsWith('/') || decoded.includes('\\') || /^[a-z]:/i.test(decoded)) return null;
  const components = (sourcePath || '').split('/').slice(0,-1);
  for(const piece of decoded.split('/')) { if(!piece || piece === '.') continue; if(piece === '..') { if(!components.length) return null; components.pop(); } else components.push(piece); }
  return {path:components.join('/') || '.'};
}
function inlineMarkdown(raw, path) {
  const tokens = [], put = html => { const key = `\uE000${tokens.length}\uE001`; tokens.push(html); return key; };
  let text = String(raw).replace(/[\uE000\uE001]/g,'');
  text = text.replace(/`([^`]+)`/g,(_,value) => put(`<code>${esc(value)}</code>`));
  text = text.replace(/!?\[([^\]]+)\]\(([^)]+)\)/g,(full,label,target) => {
    // A link label may already contain an inline-code token. Resolve it here,
    // inside text content, before storing the whole link as a new token.
    const labelHTML = esc(label).replace(/\uE000(\d+)\uE001/g,(_,index) => tokens[Number(index)] || '');
    const resolved = resolveLink(target.replace(/^<|>$/g,''),path);
    if(!resolved) return put(labelHTML);
    if(full.startsWith('!')) return put(`<span>[图片：${labelHTML}]</span>`);
    if(resolved.external) return put(`<a href="${esc(resolved.external)}" target="_blank" rel="noopener noreferrer">${labelHTML} ↗</a>`);
    return put(`<a href="#" data-source-path="${esc(resolved.path)}">${labelHTML}</a>`);
  });
  text = esc(text).replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>').replace(/(?<!\*)\*([^*]+)\*(?!\*)/g,'<em>$1</em>');
  return text.replace(/\uE000(\d+)\uE001/g,(_,index) => tokens[Number(index)] || '');
}
function markdown(raw, path) {
  const lines = String(raw).replace(/\r\n?/g,'\n').split('\n');
  const output = []; let index = 0;
  while(index < lines.length) {
    const line = lines[index];
    if(!line.trim()) { index++; continue; }
    if(/^\s*<!--/.test(line)) { let closed = line.includes('-->'); index++; while(!closed && index < lines.length) { closed = lines[index].includes('-->'); index++; } continue; }
    const fence = line.match(/^\s*(`{3,}|~{3,})/);
    if(fence) { const code = [], marker = fence[1][0], count = fence[1].length; index++; while(index < lines.length && !new RegExp(`^\\s*${marker}{${count},}\\s*$`).test(lines[index])) code.push(lines[index++]); if(index<lines.length) index++; output.push(`<pre><code>${esc(code.join('\n'))}</code></pre>`); continue; }
    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if(heading) { const level = Math.min(4,heading[1].length); output.push(`<h${level}>${inlineMarkdown(heading[2],path)}</h${level}>`); index++; continue; }
    if(/^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)) { output.push('<hr>'); index++; continue; }
    if(/^\s*>/.test(line)) { const block = []; while(index<lines.length && /^\s*>/.test(lines[index])) block.push(`<p>${inlineMarkdown(lines[index++].replace(/^\s*>\s?/,''),path)}</p>`); output.push(`<blockquote>${block.join('')}</blockquote>`); continue; }
    if(/^\s*[-*+]\s+/.test(line) || /^\s*\d+\.\s+/.test(line)) { const ordered = /^\s*\d+\.\s+/.test(line), items = [], pattern = ordered ? /^\s*\d+\.\s+/ : /^\s*[-*+]\s+/; while(index < lines.length && pattern.test(lines[index])) items.push(`<li>${inlineMarkdown(lines[index++].replace(pattern,''),path)}</li>`); const tag = ordered ? 'ol' : 'ul'; output.push(`<${tag}>${items.join('')}</${tag}>`); continue; }
    if(line.includes('|') && index+1 < lines.length && /^\s*\|?\s*:?-{3,}/.test(lines[index+1])) {
      const cells = row => row.trim().replace(/^\|/,'').replace(/\|$/,'').split('|').map(cell => cell.trim());
      const headers = cells(line), rows = []; index += 2;
      while(index<lines.length && lines[index].includes('|') && lines[index].trim()) rows.push(cells(lines[index++]));
      output.push(`<div class="table-scroll"><table><thead><tr>${headers.map(cell => `<th>${inlineMarkdown(cell,path)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${row.map(cell => `<td>${inlineMarkdown(cell,path)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`); continue;
    }
    const paragraph = [inlineMarkdown(line,path)]; index++;
    while(index<lines.length && lines[index].trim() && !/^\s*(?:#{1,6}\s|>|[-*+]\s|\d+\.\s|`{3,}|~{3,}|<!--)/.test(lines[index]) && !(lines[index].includes('|') && index+1<lines.length && /^\s*\|?\s*:?-{3,}/.test(lines[index+1]))) paragraph.push(inlineMarkdown(lines[index++],path));
    output.push(`<p>${paragraph.join('<br>')}</p>`);
  }
  return output.join('');
}
async function loadSource(path) {
  const item = node(view.selected); if(!item) return;
  const token = ++view.sourceRequest, selected = item.id;
  view.sourcePath = path || null;
  $('#readme-path').textContent = path || '尚无 README 或目录说明';
  $('#readme-back').hidden = !path || path === item.entry;
  $('#source-fingerprint').textContent = '';
  if(!path) {
    $('#readme-source').innerHTML = '<div class="empty-note"><strong>本目录还没有说明入口。</strong><p>可以向上阅读最近的 README，了解目标与约束。若这个目录需要独立管理，再接入目录说明。</p></div>';
    return;
  }
  $('#readme-source').textContent = '正在读取目录说明…';
  try {
    const result = await api(`/api/tree/source?project=${encodeURIComponent(item.project_id)}&path=${encodeURIComponent(path)}`);
    if(token !== view.sourceRequest || selected !== view.selected) return;
    $('#readme-source').innerHTML = markdown(result.content,path);
    $('#source-fingerprint').textContent = result.sha256 ? `来源文件 SHA-256 · ${result.sha256}` : '';
  } catch(error) {
    if(token !== view.sourceRequest) return;
    const warning = document.createElement('p'); warning.className = 'source-error'; warning.textContent = `暂时无法预览：${error.message}`; $('#readme-source').replaceChildren(warning);
  }
}
function search(advance = false) {
  const query = $('#tree-search').value.trim().toLocaleLowerCase(), changed = query !== view.query;
  view.query = query;
  view.matches = query ? projectNodes().filter(item => [item.title,item.directory,item.entry,item.managed ? item.purpose : ''].join(' ').toLocaleLowerCase().includes(query)) : [];
  if(changed) view.matchIndex = -1;
  $('#search-results').textContent = query ? `${view.matches.length} 项` : '';
  if(query && !view.matches.length && advance) message('没有匹配的目录，试试目录名或职责中的关键词。');
  if(view.matches.length && (advance || changed)) {
    view.matchIndex = (view.matchIndex+1)%view.matches.length;
    const found = view.matches[view.matchIndex];
    if(view.scope === 'managed' && !view.managedBranches.has(found.id)) { view.scope = 'all'; view.expanded.clear(); updateScopeButtons(); }
    expandAncestors(found);
    renderTree(); focusNode(found.id);
    $('#search-results').textContent = `${view.matchIndex+1}/${view.matches.length}`;
    if(advance) selectNode(found.id,true);
  } else renderTree();
}
async function refresh() {
  const button = $('#refresh-tree'); button.disabled = true;
  try {
    const current = view.project, selected = view.selected, expansion = new Set(view.expanded), scope = view.scope, displayDepth = view.displayDepth;
    view.data = await api('/api/tree/refresh',{method:'POST',body:'{}'});
    applyViewerCapabilities();
    populateProjects(); selectProject(view.data.projects.some(item => item.id === current) ? current : view.data.projects[0]?.id);
    view.expanded = new Set([...expansion].filter(id => node(id)));
    view.scope = scope === 'managed' && view.managedBranches.size ? 'managed' : 'all'; updateScopeButtons();
    view.displayDepth = displayDepth; $('#tree-depth').value = String(displayDepth);
    const root = node(project()?.root_id); if(root) view.expanded.add(root.id);
    if(selected && node(selected)) selectNode(selected); else renderTree();
    showWarnings(); message('目录与 Markdown 已重新读取。');
  } catch(error) { message(`刷新失败：${error.message}`,true); }
  finally { button.disabled = false; }
}
function populateProjects() {
  const select = $('#project-select'); select.replaceChildren();
  for(const item of view.data.projects || []) { const option = document.createElement('option'); option.value = item.id; option.textContent = item.title || item.id; select.append(option); }
  select.disabled = !select.options.length;
}
function showWarnings() {
  const warnings = view.data?.warnings || [];
  $('#tree-warnings').hidden = !warnings.length;
  $('#tree-warnings').textContent = warnings.map(item => typeof item === 'string' ? item : item.message || item.reason || JSON.stringify(item)).join(' · ');
}
function applyViewerCapabilities() {
  const hideDetails = view.data?.viewer?.detailed_governance === false;
  document.querySelectorAll('a[href^="/details"]').forEach(link => { link.hidden = hideDetails; });
}

$('#project-select').addEventListener('change',event => selectProject(event.target.value));
$('#scope-managed').addEventListener('click',() => changeScope('managed'));
$('#scope-all').addEventListener('click',() => changeScope('all'));
$('#tree-depth').addEventListener('change',event => setExpansionDepth(event.target.value));
$('#missing-readmes').addEventListener('click',() => {
  const missing = missingReadmes(); if(!missing.length) return;
  view.missingIndex = (view.missingIndex+1)%missing.length;
  const target = missing[view.missingIndex]; selectNode(target.id,true);
  message(`尚无 README ${view.missingIndex+1} / ${missing.length} · ${target.directory || nodeTitle(target)}，可按需接入`);
});
$('#refresh-tree').addEventListener('click',refresh);
$('#zoom-in').addEventListener('click',() => zoomBy(1.2));
$('#zoom-out').addEventListener('click',() => zoomBy(1/1.2));
$('#fit-tree').addEventListener('click',() => fitTree());
$('#readable-tree').addEventListener('click',() => { if(view.selected) { view.zoom = 1; focusNode(view.selected); } else fitTree(1); });
$('#reset-tree').addEventListener('click',() => setExpansionDepth(1));
$('#close-drawer').addEventListener('click',() => { $('#node-drawer').hidden = true; view.selected = null; view.sourceRequest++; renderTree(); });
$('#readme-back').addEventListener('click',() => loadSource(node(view.selected)?.entry));
$('#readme-source').addEventListener('click',event => {
  const link = event.target.closest('a[data-source-path]'); if(!link) return;
  event.preventDefault(); const path = link.dataset.sourcePath;
  const match = projectNodes().find(item => item.entry === path || item.directory === path || item.directory === path.replace(/\/$/,''));
  if(match) selectNode(match.id,true);
  else if(/\.md$/i.test(path)) loadSource(path);
  else message('这个链接指向普通文件。可从“打开本地文件夹”进入查看。');
});
$('#open-folder').addEventListener('click',async () => {
  const item = node(view.selected); if(!item) return;
  const button = $('#open-folder'); button.disabled = true;
  try { await api('/api/tree/open-folder',{method:'POST',body:JSON.stringify({project_id:item.project_id,directory:item.directory || '.'})}); message('已请求在本机打开文件夹。'); }
  catch(error) { message(`打开失败：${error.message}`,true); }
  finally { button.disabled = false; }
});
$('#search-form').addEventListener('submit',event => { event.preventDefault(); clearTimeout(search.timer); search(true); });
$('#tree-search').addEventListener('input',() => { clearTimeout(search.timer); search.timer = setTimeout(() => search(),160); });
$('#agent-help').addEventListener('click',() => $('#agent-dialog').showModal());
$('#close-agent').addEventListener('click',() => $('#agent-dialog').close());
$('#agent-done').addEventListener('click',() => $('#agent-dialog').close());
$('#agent-dialog').addEventListener('click',event => { if(event.target === $('#agent-dialog')) { const rect = event.target.getBoundingClientRect(); if(event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) event.target.close(); } });
const canvas = $('#tree-canvas');
canvas.addEventListener('pointerdown',event => {
  if(event.button !== 0) return;
  view.drag = {pointer:event.pointerId,startX:event.clientX,startY:event.clientY,x:view.x,y:view.y}; view.moving = false;
});
canvas.addEventListener('pointermove',event => {
  if(!view.drag || event.pointerId !== view.drag.pointer) return;
  const dx = event.clientX-view.drag.startX, dy = event.clientY-view.drag.startY;
  if(!view.moving && Math.hypot(dx,dy) > 5) { view.moving = true; canvas.setPointerCapture(event.pointerId); canvas.classList.add('is-dragging'); }
  if(view.moving) { view.x = view.drag.x+dx; view.y = view.drag.y+dy; applyTransform(); }
});
function finishDrag(event) {
  if(!view.drag || event.pointerId !== view.drag.pointer) return;
  view.suppressClick = view.moving;
  if(canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
  view.drag = null; view.moving = false; canvas.classList.remove('is-dragging');
  setTimeout(() => { view.suppressClick = false; },0);
}
canvas.addEventListener('pointerup',finishDrag); canvas.addEventListener('pointercancel',finishDrag);
canvas.addEventListener('pointerleave',event => { if(!view.moving) finishDrag(event); });
canvas.addEventListener('wheel',event => { event.preventDefault(); if(event.ctrlKey || event.metaKey) zoomBy(Math.exp(-event.deltaY*.002),event.clientX,event.clientY); else { view.x -= event.deltaX; view.y -= event.deltaY; applyTransform(); } },{passive:false});
canvas.addEventListener('keydown',event => {
  if(event.target !== canvas) return;
  const delta = 50;
  if(event.key === 'ArrowLeft') view.x += delta; else if(event.key === 'ArrowRight') view.x -= delta; else if(event.key === 'ArrowUp') view.y += delta; else if(event.key === 'ArrowDown') view.y -= delta; else if(event.key === '+' || event.key === '=') { zoomBy(1.2); event.preventDefault(); return; } else if(event.key === '-') { zoomBy(1/1.2); event.preventDefault(); return; } else if(event.key === 'Home') { fitTree(); event.preventDefault(); return; } else return;
  event.preventDefault(); applyTransform();
});
document.addEventListener('keydown',event => { if(event.key === '/' && !['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName) && !$('#agent-dialog').open) { event.preventDefault(); $('#tree-search').focus(); } else if(event.key === 'Escape' && !$('#agent-dialog').open && !$('#node-drawer').hidden) $('#close-drawer').click(); });
window.addEventListener('resize',() => { clearTimeout(view.resizeTimer); view.resizeTimer = setTimeout(() => { if(view.selected) focusNode(view.selected); },120); });

(async function boot() {
  try {
    view.data = await api('/api/tree'); populateProjects(); showWarnings(); applyViewerCapabilities();
    if(!view.data.projects?.length) { showCanvasMessage('还没有接入项目','在项目配置中添加目录，然后刷新。'); return; }
    let saved;
    try { saved = localStorage.getItem('doctree.tree.project'); } catch (_) { /* Preferences do not affect data. */ }
    const params = new URLSearchParams(location.search);
    const selectedProject = view.data.projects.find(item => item.id === params.get('project')) || view.data.projects.find(item => item.id === saved) || view.data.projects[0];
    selectProject(selectedProject.id,true);
    const requestedNode = params.get('node'); if(requestedNode && node(requestedNode)) selectNode(requestedNode,true);
  } catch(error) { showCanvasMessage('暂时没有读到目录地图',`${error.message} 可以点击刷新重试；也可以打开详细视图。`,true); message(error.message,true); }
})();

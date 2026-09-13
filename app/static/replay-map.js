/* Historical replay presentation. Uses saved observations; no analysis jobs are started here. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const state = {caseId:'kharkiv', snapshot:null, metadata:null, current:null, map:null,
    cache:new Map(), request:0, detailRequest:0, popup:null, reportTab:'overview', station:null, busy:false,
    layerChoice:{gnss:true,shipping:true,aircraft:false}};
  const layers = {};
  const finite = (value) => typeof value === 'number' && Number.isFinite(value);
  const number = (value) => finite(value) ? value.toLocaleString('zh-CN',{maximumFractionDigits:2}) : '—';
  const stamp = (value) => value ? String(value).replace('T',' ').replace(/(?:\.\d+)?Z$/,' UTC') : '时间未注明';
  const day = (value) => value ? String(value).slice(0,10) : '—';
  const short = (value, length=115) => { const text=String(value || ''); return text.length>length ? `${text.slice(0,length)}…` : text; };
  const names = {gnss:'GNSS 观测',portwatch:'航运观测',news:'公开报道',aircraft:'航空活动'};
  const decisions = {insufficient_evidence:'证据不足',no_anomaly:'未形成新的异常线索',new:'新发现',update:'结论更新',revised:'结论修订',active:'持续预警',recovering:'恢复中',resolved:'预警已解除',revoked:'预警已撤销'};
  function node(tag, className, text) {
    const element=document.createElement(tag); if(className)element.className=className;
    if(text!=null)element.textContent=text; return element;
  }
  function button(text, callback, className='evidence-button') {
    const element=node('button',className,text); element.type='button';
    element.addEventListener('click',()=>Promise.resolve().then(callback).catch(showError)); return element;
  }
  async function get(url) {
    const response=await fetch(url); if(!response.ok)throw new Error('资料暂时无法读取，请稍后重试。');
    return response.json();
  }
  function showError(error) { $('map-loading').hidden=false; $('map-loading').textContent=error.message || '页面暂时无法加载。'; }
  function caseMeta() { return state.metadata.cases[state.caseId]; }
  function caseName() { return state.caseId==='kharkiv' ? '哈尔科夫' : '霍尔木兹海峡'; }
  function coordinates(item) { return item.coordinates || item.geometry?.coordinates; }
  function latLng(item) { const point=coordinates(item); return [point[1],point[0]]; }
  function primaryAlert(alerts) {
    const rank=(item)=> item.origin_type==='numeric_rule' && ['active','recovering'].includes(item.status) ? 0 : ['resolved','revoked'].includes(item.status) ? 2 : 1;
    return [...alerts].sort((a,b)=>rank(a)-rank(b) || String(b.updated_at || '').localeCompare(String(a.updated_at || '')))[0];
  }
  function gnssRows(station) {
    return (state.current?.view.gnss?.series || []).filter((row)=>(!station || row.station===station) && (row.unit==='dB-Hz' || finite(row.p10) || finite(row.cnr_p10)))
      .map((row)=>({...row,time:row.time || row.window_start,p10:row.p10 ?? row.cnr_p10,delta:row.delta_db ?? row.delta_p10}))
      .sort((a,b)=>String(a.time).localeCompare(String(b.time)));
  }
  function conclusion() {
    const current=state.current, alert=current?.alert, work=current?.view.recent_work || [];
    if(!current)return null;
    const states={
      normal:{status:'绿色 · 监测判定',title:'判定正常',summary:'当前观测判定正常，持续执行监测。',tone:'normal'},
      attention:{status:'黄色 · 监测判定',title:'发现可疑信号',summary:'需扩大证据范围，持续核实与论证。',tone:'attention'},
      warning:{status:'红色 · 监测判定',title:'明确预警信号',summary:'已达到预警条件，持续跟踪后续变化。',tone:'warning'}
    };
    const result=(tone,evidence,observed=current.view.as_of)=>({...states[tone],evidence,observed});
    const maritime=current.metrics?.state;
    const latestValue=maritime?.latest_value ?? alert?.latest_observation?.n_total;
    const baseline=maritime?.baseline ?? alert?.baseline;
    const shippingEvidence=[];
    if(finite(latestValue) && finite(baseline))shippingEvidence.push(`海峡最新日通行量为 ${number(latestValue)} 艘次，历史参考为 ${number(baseline)} 艘次。`);
    if(alert?.origin_type==='numeric_rule' && ['active','recovering'].includes(alert.status)) {
      const rule=alert.rule;
      if(rule?.trigger_days && finite(rule.trigger_ratio))shippingEvidence.push(`通行量连续 ${rule.trigger_days} 天低于历史参考的 ${number(rule.trigger_ratio*100)}%，达到预警条件。`);
      shippingEvidence.push(alert.status==='recovering'?'通行量开始回升，但尚未满足持续恢复条件。':'通行量持续偏低，尚未满足预警解除条件。');
      return result('warning',shippingEvidence,alert.latest_observation?.observed_date || alert.last_evaluated_date);
    }
    // Use the latest assessment batch for each source, not superseded historical decisions.
    const latestBySource=new Map();
    work.forEach((item)=>{if(item.decision && (!latestBySource.has(item.kind) || item.as_of>latestBySource.get(item.kind)))latestBySource.set(item.kind,item.as_of);});
    const assessments=work.filter((item)=>item.decision && item.as_of===latestBySource.get(item.kind));
    const latestRows=new Map();
    gnssRows().forEach((row)=>latestRows.set(`${row.station}/${row.signal}`,row));
    const comparable=[...latestRows.values()].filter((row)=>finite(row.delta) && finite(row.p10));
    const stationCount=new Set(comparable.map((row)=>row.station)).size;
    const lowerCount=new Set(comparable.filter((row)=>row.delta<0).map((row)=>row.station)).size;
    const gnssEvidence=lowerCount?[`最新可比观测中，${stationCount} 个 IGS 站里有 ${lowerCount} 个站的部分卫星信号质量（CNR）低于历史参考。`]:[];
    const limited=assessments.some((item)=>item.decision==='insufficient_evidence');
    const clue=alert && !['resolved','revoked'].includes(alert.status);
    const candidate=assessments.some((item)=>['candidate','update'].includes(item.decision));
    const shippingCandidate=maritime?.current_status==='candidate';
    const evidence=[...gnssEvidence];
    if(shippingCandidate)evidence.push(...shippingEvidence,'海峡通行量单日偏低，需要继续观察是否持续。');
    if(clue)evidence.push(alert.origin_type==='news_clue'?'公开报道中发现需核实的事件线索，详见来源报告。':'已有观测分析发现待核实线索，详见分析报告。');
    if(clue || candidate || shippingCandidate || (limited && lowerCount)) {
      if(lowerCount)evidence.push('信号下降的原因及影响范围仍需更多观测佐证。');
      return result('attention',evidence.length?evidence:['已有来源分析发现待核实线索，需结合更多资料判断。']);
    }
    if(assessments.length && assessments.every((item)=>item.decision==='no_anomaly') && (!maritime || ['not_triggered','resolved'].includes(maritime.current_status))) {
      const normalEvidence=[];
      if(assessments.some((item)=>item.kind==='gnss'))normalEvidence.push('最新卫星信号观测经分析，未发现异常变化。');
      if(maritime)normalEvidence.push(...shippingEvidence,maritime.current_status==='resolved'?'海峡通行量已满足持续恢复条件。':'海峡通行量未达到偏低预警条件。');
      if(assessments.some((item)=>item.kind==='news'))normalEvidence.push('最新公开报道经分析，未发现新的异常线索。');
      return result('normal',normalEvidence);
    }
    // Missing evidence is not a normal finding or a suspicious signal by itself.
    return null;
  }
  function initializeMap(world) {
    const map=state.map=L.map('map',{zoomControl:false,attributionControl:false,minZoom:2,maxZoom:9,worldCopyJump:false,preferCanvas:true});
    L.control.zoom({position:'bottomright',zoomInTitle:'放大地图',zoomOutTitle:'缩小地图'}).addTo(map);
    L.geoJSON(world,{style:{color:'#3a4952',weight:.7,fillColor:'#1b2831',fillOpacity:1},interactive:false}).addTo(map);
    const countryNames={UKR:'乌克兰',POL:'波兰',SVK:'斯洛伐克',HUN:'匈牙利',ROU:'罗马尼亚',BLR:'白俄罗斯',RUS:'俄罗斯',IRN:'伊朗',OMN:'阿曼',ARE:'阿联酋',SAU:'沙特阿拉伯',IRQ:'伊拉克',TUR:'土耳其',CYP:'塞浦路斯',ISR:'以色列',UZB:'乌兹别克斯坦',GEO:'格鲁吉亚',AZE:'阿塞拜疆',TKM:'土库曼斯坦',DEU:'德国',CZE:'捷克'};
    L.geoJSON(world,{onEachFeature(feature,layer){
      const code=feature.properties.iso_a3;
      if(countryNames[code] && code!=='RUS')L.marker(layer.getBounds().getCenter(),{interactive:false,keyboard:false,icon:L.divIcon({className:'place-label',html:countryNames[code],iconSize:[110,22],iconAnchor:[55,11]})}).addTo(map);
    }});
    const grid=L.layerGroup().addTo(map);
    for(let lng=-20;lng<=90;lng+=10)L.polyline([[-5,lng],[75,lng]],{color:'#304651',opacity:.3,weight:.6,dashArray:'2 8',interactive:false}).addTo(grid);
    for(let lat=0;lat<=70;lat+=10)L.polyline([[lat,-20],[lat,90]],{color:'#304651',opacity:.3,weight:.6,dashArray:'2 8',interactive:false}).addTo(grid);
    ['gnss','shipping','aircraft','focus'].forEach((name)=>{layers[name]=L.layerGroup().addTo(map);});
  }
  function fitCase() {
    const meta=caseMeta(), points=[latLng(meta.focus),...(meta.stations || []).map(latLng)];
    const width=state.map.getSize().x,narrow=width<700,reportOpen=!$('report-panel').hidden;
    state.map.fitBounds(L.latLngBounds(points).pad(.23),{paddingTopLeft:narrow?[18,145]:[Math.min(255,width*.18),110],
      paddingBottomRight:narrow?[18,120]:[Math.min(reportOpen?550:345,width*.28),95],maxZoom:6,animate:false});
    if(state.popup){state.popup.openOn(state.map);state.popup.update();}
  }
  function toggleLayer(kind) {
    if($(`layer-${kind}`).checked)layers[kind].addTo(state.map);else state.map.removeLayer(layers[kind]);
  }
  function renderMapFeatures() {
    const meta=caseMeta(), current=state.current;
    Object.values(layers).forEach((layer)=>layer.clearLayers());
    const availableStations=new Set((current?.view.gnss?.summary || []).map((item)=>item.station));
    (meta.stations || []).forEach((station)=>{
      const id=station.station || station.id;
      const marker=L.circleMarker(latLng(station),{radius:5,color:'#8ae5e0',weight:1.3,fillColor:'#3fc2c4',fillOpacity:.8});
      marker.bindTooltip(id,{permanent:true,direction:'right',offset:[7,0],className:'station-label'});
      marker.on('click',()=>openReport('gnss',id)); marker.addTo(layers.gnss);
    });
    const focus=L.circleMarker(latLng(meta.focus),{radius:12,color:'#68d7db',weight:1.3,fillColor:'#5dc7cd',fillOpacity:.1}).addTo(layers.focus);
    focus.bindTooltip(caseName(),{permanent:true,direction:'bottom',offset:[0,12],className:'focus-label'});
    L.circleMarker(latLng(meta.focus),{radius:3,color:'#c6f7f3',weight:1,fillOpacity:1}).addTo(layers.focus);
    focus.on('click',()=>state.popup?.openOn(state.map));
    const aggregate=(meta.regions || []).find((item)=>item.aggregate);
    if(aggregate && current?.metrics?.observations?.length) {
      const point=latLng(aggregate), value=current.metrics.state?.latest_value;
      L.circleMarker(point,{radius:24,color:'#d6a169',weight:1,fillColor:'#bb8248',fillOpacity:.15}).on('click',()=>openReport('portwatch')).addTo(layers.shipping);
      const text=node('div','shipping-marker'); text.append(node('strong','',number(value)),node('span','','船／航次'));
      L.marker(point,{icon:L.divIcon({className:'shipping-marker-wrap',html:text,iconSize:[74,46],iconAnchor:[37,-28]})}).on('click',()=>openReport('portwatch')).addTo(layers.shipping);
    }
    // Both cases use the same layers. Individual positions only appear when the archive contains tracks.
    for(const kind of ['aircraft','shipping']) {
      const tracks=meta.tracks?.[kind==='shipping'?'vessels':kind] || [];
      tracks.forEach((track)=>{
        if(track.coordinates?.length>1)L.polyline(track.coordinates.map((point)=>[point[1],point[0]]),{color:kind==='aircraft'?'#70bce7':'#d6a169',weight:1.5}).addTo(layers[kind]);
      });
    }
    $('gnss-layer-status').textContent=`${availableStations.size || (meta.stations || []).length} 个观测站`;
    $('aircraft-layer-status').textContent=meta.tracks?.aircraft?.length?'历史航迹':'本例未收录航迹';
    $('shipping-layer-status').textContent=current?.metrics?.observations?.length?'海峡日通行量':'本例未收录航运观测';
    $('layer-aircraft').disabled=!meta.tracks?.aircraft?.length;
    $('layer-shipping').disabled=!(current?.metrics?.observations?.length || meta.tracks?.vessels?.length);
    ['gnss','aircraft','shipping'].forEach((kind)=>{$(`layer-${kind}`).checked=!$(`layer-${kind}`).disabled && state.layerChoice[kind];});
    $('layer-note').textContent='点击站点或预警框查看依据';
    ['gnss','aircraft','shipping'].forEach(toggleLayer);
    renderCallout();
  }
  function renderCallout() {
    const data=conclusion();
    if(!data){if(state.popup)state.map.closePopup(state.popup);state.popup=null;$('case-date').textContent=`历史观测 · ${day(state.current?.view.as_of)}`;$('map-loading').hidden=false;$('map-loading').textContent='当前观测资料尚不足以作出监测判定';return;}
    const content=node('div','map-callout'); content.id='map-callout';content.dataset.tone=data.tone;
    const action=button('',()=>openReport('overview'),'callout-main'); action.id='case-conclusion';
    action.append(node('span','callout-status',data.status),node('h2','callout-title',data.title),node('p','callout-summary',data.summary));
    const evidence=node('ul','callout-evidence');data.evidence.forEach((item)=>evidence.append(node('li','',item)));
    action.append(node('span','callout-evidence-label','判定依据'),evidence,node('span','callout-open','查看分析报告 →'));content.append(action);
    if(!state.popup)state.popup=L.popup({className:'case-popup',closeButton:false,closeOnClick:false,autoClose:false,minWidth:240,maxWidth:320,
      autoPanPaddingTopLeft:[12,145],autoPanPaddingBottomRight:[12,100],offset:[18,-9]});
    state.popup.setLatLng(latLng(caseMeta().focus)).setContent(content).openOn(state.map);
    $('case-date').textContent=`历史观测 · ${day(data.observed)}`;
  }
  async function loadCase(recenter=false) {
    const request=++state.request, id=state.caseId;
    const summary=state.snapshot?.replay?.cases?.find((item)=>item.case_id===id);
    if(!summary)throw new Error('此入口未配置历史案例，请打开历史回放服务。');
    const signature=JSON.stringify([summary,(state.snapshot.alerts || []).filter((item)=>item.case_id===id)]);
    let cached=state.cache.get(id);
    if(cached?.signature!==signature) {
      const view=await get(`/api/replay/cases/${encodeURIComponent(id)}`);
      const alert=primaryAlert(view.alerts || []);
      const shipping=state.metadata.cases[id].regions?.some((item)=>item.aggregate);
      const [detail,metrics]=await Promise.all([alert?get(`/api/alerts/${encodeURIComponent(alert.id)}`):null,
        shipping && view.portwatch_monitor_id?get(`/api/monitors/${encodeURIComponent(view.portwatch_monitor_id)}/metrics`):null]);
      cached={signature,view,alert:detail,metrics};state.cache.set(id,cached);
    }
    if(request!==state.request || id!==state.caseId)return;
    const changed=state.current!==cached;state.current=cached;
    $('map-loading').hidden=true;
    if(changed)renderMapFeatures();
    if(recenter)fitCase();
  }
  async function selectCase(id) {
    if(!state.metadata?.cases[id])return;
    closeReport(); state.request++; state.caseId=id;state.current=null;
    if(state.popup){state.map.closePopup(state.popup);state.popup=null;}
    Object.values(layers).forEach((layer)=>layer.clearLayers());
    document.querySelectorAll('[data-case]').forEach((element)=>{element.classList.toggle('active',element.dataset.case===id);element.setAttribute('aria-pressed',String(element.dataset.case===id));});
    $('map-loading').textContent='正在打开案例…';$('map-loading').hidden=false;
    fitCase();await loadCase(true);
  }
  function section(title,parent=$('report-body')) {
    const block=node('section','report-section');block.append(node('h3','',title));parent.append(block);return block;
  }
  function metrics(values,parent) {
    const grid=node('div','report-metrics');values.forEach(([label,value])=>{const item=node('div');item.append(node('span','',label),node('strong','',value));grid.append(item);});parent.append(grid);
  }
  function sourceLink(url,title,parent) {
    if(!url)return;let address;try{address=new URL(url);}catch{return;}
    if(!['https:','http:'].includes(address.protocol))return;
    const link=node('a','source-link',title || '查看来源');link.href=address.href;link.target='_blank';link.rel='noopener';parent.append(link);
  }
  function references(refs,parent) {
    const seen=new Set();(refs || []).forEach((ref,index)=>{
      const record=typeof ref==='string'?{id:ref}:ref, id=record.material_id || record.id;
      if(id && !seen.has(id)){seen.add(id);parent.append(button(record.title || `引用资料 ${index+1}`,()=>openMaterial(id)));}
      else if(record.url)sourceLink(record.url,record.title,parent);
    });
  }
  function openReport(tab='overview',station=null) {
    if(!state.current)return;state.detailRequest++;state.reportTab=tab;state.station=station;
    $('report-panel').hidden=false;document.body.classList.add('report-open');$('report-back').hidden=true;
    $('report-title').textContent=`${caseName()} · 分析报告`;renderReport();$('report-close').focus();
  }
  function closeReport() {
    const opened=!$('report-panel').hidden;state.detailRequest++;$('report-panel').hidden=true;
    document.body.classList.remove('report-open');if(opened)$('case-conclusion')?.focus({preventScroll:true});
  }
  function renderReport() {
    const parent=$('report-body');parent.replaceChildren();parent.scrollTop=0;
    const nav=node('nav','report-tabs');nav.setAttribute('aria-label','报告与证据');
    [['overview','综合研判'],['gnss','GNSS'],['portwatch','航运'],['news','公开报道']].forEach(([id,title])=>{
      const tab=button(title,()=>{state.detailRequest++;state.reportTab=id;state.station=null;renderReport();},state.reportTab===id?'active':'');
      tab.setAttribute('aria-current',state.reportTab===id?'page':'false');nav.append(tab);
    });parent.append(nav);
    if(state.reportTab==='overview')renderOverview();
    else if(state.reportTab==='gnss')renderGnss();
    else if(state.reportTab==='portwatch')renderShipping();
    else renderNews();
  }
  function renderOverview() {
    const data=conclusion(), alert=state.current.alert;
    const overview=section('区域结论');
    if(data)overview.append(node('span',`report-status ${data.tone}`,data.status),node('h2','report-conclusion',data.title),node('p','',data.summary));
    else overview.append(node('p','','当前观测资料尚不足以作出监测判定。'));
    overview.append(node('p','report-date',`观测日期：${day(data?.observed || state.current.view.as_of)}`));
    if(alert?.analysis?.statement && !alert.analysis_stale)section('综合分析').append(node('p','',alert.analysis.statement));
    const sourceAssessments=new Map();
    (state.current.view.recent_work || []).forEach((work)=>{if(work.statement && !sourceAssessments.has(work.kind))sourceAssessments.set(work.kind,work);});
    if(sourceAssessments.size){const assessments=section('各来源研判');sourceAssessments.forEach((work,kind)=>{
      const item=node('div','source-assessment');item.append(node('strong','',names[kind] || '观测资料'),node('p','',work.statement));assessments.append(item);
    });}
    const evidence=section('支持证据');
    if(alert?.origin_type==='numeric_rule')metrics([['最新通行量',number(alert.latest_observation?.n_total)],['历史参考',number(alert.baseline)],['触发日期',`${day(alert.started_on)} 起`]],evidence);
    (data?.evidence || []).forEach((text)=>evidence.append(node('p','',text)));
    const sources=node('div','evidence-list');
    [['gnss','查看 GNSS 观测与分析'],['portwatch','查看航运观测与分析'],['news','查看公开报道与分析']].forEach(([id,title])=>sources.append(button(title,()=>{state.reportTab=id;state.station=null;renderReport();})));
    evidence.append(sources);
    if(alert?.evidence_refs?.length)references(alert.evidence_refs,section('原始引用'));
    const other=(state.current.view.alerts || []).filter((item)=>item.id!==alert?.id);
    if(other.length){const block=section('其他区域线索');other.forEach((item)=>block.append(button(item.title || '查看线索',()=>openAlert(item.id))));}
  }
  function plot(rows,series,parent,unit) {
    const good=rows.filter((row)=>Number.isFinite(Date.parse(row.time))), values=good.flatMap((row)=>series.map((s)=>row[s.key]).filter(finite));
    if(!values.length){parent.append(node('p','report-empty','当前资料中没有可绘制的观测。'));return;}
    const ns='http://www.w3.org/2000/svg';
    const svg=(tag,attrs,text)=>{const item=document.createElementNS(ns,tag);Object.entries(attrs).forEach(([key,value])=>item.setAttribute(key,value));if(text!=null)item.textContent=text;return item;};
    const width=440,height=200,left=40,right=14,top=16,bottom=38;
    const chart=svg('svg',{viewBox:`0 0 ${width} ${height}`,role:'img','aria-label':`历史观测曲线，单位 ${unit}`});
    let low=Math.min(...values),high=Math.max(...values);const span=Math.max(1,high-low);low-=span*.12;high+=span*.12;
    const start=Date.parse(good[0].time),end=Date.parse(good.at(-1).time);
    const x=(row)=>left+(Date.parse(row.time)-start)/Math.max(1,end-start)*(width-left-right),y=(value)=>height-bottom-(value-low)/(high-low)*(height-top-bottom);
    for(let tick=0;tick<=3;tick++){const value=low+(high-low)*tick/3;chart.append(svg('line',{x1:left,y1:y(value),x2:width-right,y2:y(value),stroke:'#34414b','stroke-width':.7}),svg('text',{x:left-7,y:y(value)+4,'text-anchor':'end'},number(value)));}
    const gaps=good.slice(1).map((row,i)=>Date.parse(row.time)-Date.parse(good[i].time)).filter((n)=>n>0).sort((a,b)=>a-b),cadence=gaps[Math.floor(gaps.length/2)] || 86400000;
    series.forEach((line)=>{let path='',previous=null;good.forEach((row)=>{
      if(!finite(row[line.key])){previous=null;return;}
      const contiguous=previous && !row.gap && !previous.gap && Date.parse(row.time)-Date.parse(previous.time)<=cadence*1.5;
      path+=`${contiguous?'L':'M'}${x(row).toFixed(1)},${y(row[line.key]).toFixed(1)} `;previous=row;
    });chart.append(svg('path',{d:path,fill:'none',stroke:line.color,'stroke-width':1.7,...(line.dashed?{'stroke-dasharray':'5 4'}:{})}));});
    [good[0],good.at(-1)].forEach((row,i)=>chart.append(svg('text',{x:i?width-right:left,y:height-12,'text-anchor':i?'end':'start'},day(row.time))));
    const wrap=node('div','report-chart');wrap.append(chart);parent.append(wrap);
    const legend=node('div','chart-legend');series.forEach((line)=>{const item=node('span','',line.title);item.style.color=line.color;legend.append(item);});legend.append(node('span','',unit));parent.append(legend);
  }
  function renderGnss() {
    const meta=caseMeta(), block=section('GNSS 观测证据'), rows=gnssRows();
    if(meta.spatial_note)block.append(node('p','report-date',meta.spatial_note));
    const stations=[...new Set(rows.map((row)=>row.station))];
    if(!stations.length){block.append(node('p','report-empty','本例尚无可展示的 GNSS 数值。'));renderWorkList('gnss');return;}
    if(!stations.includes(state.station))state.station=stations[0];
    const label=node('label','report-label','观测站与信号'),select=node('select','report-select');select.setAttribute('aria-label','选择观测站和信号');
    const groups=[...new Set(rows.map((row)=>`${row.station} / ${row.signal}`))];
    groups.forEach((key)=>select.add(new Option(key,key)));select.value=groups.find((key)=>key.startsWith(`${state.station} / `));
    block.append(label,select);const details=node('div');block.append(details);
    const draw=()=>{
      details.replaceChildren();const key=select.value, data=rows.filter((row)=>`${row.station} / ${row.signal}`===key),latest=data.at(-1);
      const station=(meta.stations || []).find((item)=>(item.station || item.id)===latest?.station);
      if(station){const c=coordinates(station);details.append(node('p','report-date',`实际站点位置 ${number(c[1])}°N，${number(c[0])}°E`));}
      metrics([['CNR p10',`${number(latest?.p10)} dB-Hz`],['可比历史',finite(latest?.baseline_p10)?`${number(latest.baseline_p10)} dB-Hz`:'参考不足'],['相对参考',finite(latest?.delta)?`${number(latest.delta)} dB`:'暂无可比值']],details);
      plot(data,[{key:'p10',title:'观测信号',color:'#73d7d0'},...(data.some((row)=>finite(row.baseline_p10))?[{key:'baseline_p10',title:'可比历史',color:'#bf9f79',dashed:true}]:[])],details,'dB-Hz');
      details.append(node('p','report-date',`最新窗口：${stamp(latest?.time)}。曲线按同站同信号比较，缺测处断开。`));
      if(latest?.material_id)details.append(button('查看该窗口原始依据',()=>openMaterial(latest.material_id)));
    };
    select.addEventListener('change',draw);draw();renderWorkList('gnss');
  }
  function renderShipping() {
    const data=state.current.metrics,alert=state.current.alert?.origin_type==='numeric_rule'?state.current.alert:null;
    const block=section('航运观测证据');
    if(!data?.observations?.length){block.append(node('p','report-empty','本例未收录航运观测。'));renderWorkList('portwatch');return;}
    const latest=data.state,baseline=alert?.baseline ?? latest?.baseline;
    metrics([['最新通行量',number(latest?.latest_value)],['历史参考',number(baseline)],['相对参考',finite(latest?.ratio)?`${number(latest.ratio*100)}%`:'—']],block);
    const rows=data.observations.map((row)=>({...row,time:row.observed_date,baseline})).sort((a,b)=>a.time.localeCompare(b.time));
    plot(rows,[{key:'n_total',title:'可见通行量',color:'#dbae78'},{key:'baseline',title:'历史参考',color:'#6aaab2',dashed:true}],block,'船／航次');
    block.append(node('p','report-date','PortWatch 海峡日度汇总。地图标记表示观测区域，数值单位为船／航次。'));
    if(alert){const trigger=section('异常判据');trigger.append(node('p','',`连续 ${alert.rule.trigger_days} 天低于历史参考的 ${number(alert.rule.trigger_ratio*100)}%。参考时段为 ${alert.baseline_start} 至 ${alert.baseline_end}。`));
      (alert.trigger_observations || []).forEach((row)=>trigger.append(button(`${row.observed_date} · ${row.n_total} 船／航次 · 查看依据`,()=>openMaterial(row.material_id))));}
    const daily=section('日度数据');const table=node('table','report-table'),head=node('tr');['日期','通行量','原始资料'].forEach((title)=>head.append(node('th','',title)));table.append(head);
    [...rows].reverse().forEach((row)=>{const tr=node('tr'),link=node('td');if(row.material_id)link.append(button('查看',()=>openMaterial(row.material_id),'text-button'));tr.append(node('td','',row.observed_date),node('td','',number(row.n_total)),link);table.append(tr);});daily.append(table);renderWorkList('portwatch');
  }
  function renderNews() {
    const works=(state.current.view.recent_work || []).filter((work)=>work.kind==='news' && work.statement);
    if(!works.length)section('公开报道').append(node('p','report-empty','本例未收录可展示的报道分析。'));
    renderWorkList('news');
  }
  function renderWorkList(kind) {
    const works=(state.current.view.recent_work || []).filter((work)=>work.kind===kind && work.statement);
    if(!works.length)return;
    const block=section(`${names[kind]}分析报告`),list=node('div','evidence-list');
    works.forEach((work)=>{const item=button('',()=>openWork(work.id));item.append(node('span','report-date',day(work.as_of)),node('strong','',decisions[work.decision] || '观测分析'),node('p','',short(work.statement,135)));list.append(item);});block.append(list);
  }
  async function detailView(title,url,render) {
    const request=++state.detailRequest,caseId=state.caseId;
    $('report-panel').hidden=false;document.body.classList.add('report-open');$('report-title').textContent=title;$('report-back').hidden=false;
    $('report-body').replaceChildren(node('p','report-empty','正在打开资料…'));
    try{const data=await get(url);if(request!==state.detailRequest || caseId!==state.caseId || $('report-panel').hidden)return;
      $('report-body').replaceChildren();$('report-body').scrollTop=0;render(data);
    }catch(error){if(request===state.detailRequest)$('report-body').replaceChildren(node('p','report-empty',error.message));}
  }
  function openWork(id) {
    return detailView('详细分析报告',`/api/work/${encodeURIComponent(id)}`,(work)=>{
      const assessment=work.assessment || {},block=section(names[work.kind] || '观测分析');
      block.append(node('span','report-status',decisions[assessment.decision] || '分析结论'),node('h2','report-conclusion',assessment.title || '观测与研判'),node('p','',assessment.statement || '当前资料未形成分析结论。'),node('p','report-date',`历史观测截至 ${stamp(work.as_of)}`));
      const limits=Array.isArray(assessment.limitations)?assessment.limitations:assessment.limitations?[assessment.limitations]:[];
      if(limits.length){const limitsBlock=section('证据适用范围');limits.forEach((text)=>limitsBlock.append(node('p','',text)));}
      const materials=work.input?.materials || [],refs=assessment.evidence_refs || [];
      if(materials.length || refs.length){const refsBlock=section('来源与原始资料');
        const byId=new Map(materials.map((item)=>[item.id,item]));references(refs.map((ref)=>({...byId.get(typeof ref==='string'?ref:ref.material_id || ref.id),...(typeof ref==='string'?{id:ref}:ref)})),refsBlock);
        if(!refs.length)references(materials,refsBlock);}
    });
  }
  function openMaterial(id) {
    if(!id)return;
    return detailView('原始证据',`/api/materials/${encodeURIComponent(id)}`,(material)=>{
      const block=section(material.publisher || names[material.source] || '公开来源');
      block.append(node('h2','report-conclusion',material.title || '原始观测资料'),node('p','report-date',`资料时间：${stamp(material.observed_at || material.available_at)}`));
      sourceLink(material.url,'打开原始来源',block);
      if(material.analysis?.summary)section('资料摘要').append(node('p','',typeof material.analysis.summary==='string'?material.analysis.summary:JSON.stringify(material.analysis.summary)));
      section('来源内容').append(node('p','source-excerpt',material.text || '该资料为结构化观测，数值与曲线见对应证据页。'));
    });
  }
  function openAlert(id) {
    return detailView('区域异常分析',`/api/alerts/${encodeURIComponent(id)}`,(alert)=>{
      const block=section('异常结论');block.append(node('h2','report-conclusion',alert.title || '区域异常'),node('p','',alert.analysis?.statement || alert.summary || alert.statement));
      if(alert.evidence_refs?.length)references(alert.evidence_refs,section('支持资料'));
    });
  }
  async function refresh() {
    if(document.hidden || state.busy || !state.metadata)return;state.busy=true;
    try{state.snapshot=await get('/api/pipeline');await loadCase();}catch(error){showError(error);}finally{state.busy=false;}
  }
  async function start() {
    const [metadata,world,snapshot]=await Promise.all([get('/static/replay-map-data.json'),get('/static/replay-world.geojson'),get('/api/pipeline')]);
    state.metadata=metadata;state.snapshot=snapshot;initializeMap(world);
    document.querySelectorAll('[data-case]').forEach((element)=>element.addEventListener('click',()=>selectCase(element.dataset.case).catch(showError)));
    ['gnss','aircraft','shipping'].forEach((kind)=>$(`layer-${kind}`).addEventListener('change',()=>{state.layerChoice[kind]=$(`layer-${kind}`).checked;toggleLayer(kind);}));
    $('map-home-btn').addEventListener('click',fitCase);$('report-close').addEventListener('click',closeReport);
    $('report-back').addEventListener('click',()=>openReport(state.reportTab,state.station));
    document.addEventListener('keydown',(event)=>{if(event.key==='Escape')closeReport();});
    document.addEventListener('visibilitychange',refresh);
    let resizeTimer;
    window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{state.map.invalidateSize();fitCase();},120);});
    if(window.innerWidth<700)$('layer-fold').open=false;
    await selectCase('kharkiv');setInterval(refresh,15000);
  }
  start().catch(showError);
})();

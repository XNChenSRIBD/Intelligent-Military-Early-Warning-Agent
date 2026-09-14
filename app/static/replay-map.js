/* Historical replay presentation. Uses saved observations; no analysis jobs are started here. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const state = {caseId:'kharkiv', metadata:null, current:null, map:null,
    request:0, detailRequest:0, popup:null, reportTab:'overview', station:null, busy:false, at:null,
    gnssArchive:null, gnssRequest:0, gnssInitialDate:null,
    layerChoice:{gnss:true,shipping:true,aircraft:false}};
  const layers = {};
  const finite = (value) => typeof value === 'number' && Number.isFinite(value);
  const number = (value) => finite(value) ? value.toLocaleString('zh-CN',{maximumFractionDigits:2}) : '—';
  const stamp = (value) => value ? String(value).replace('T',' ').replace(/(?:\.\d+)?(?:Z|\+00:00)$/,' UTC') : '时间未注明';
  const day = (value) => value ? String(value).slice(0,10) : '—';
  const short = (value, length=115) => { const text=String(value || ''); return text.length>length ? `${text.slice(0,length)}…` : text; };
  const names = {gnss:'GNSS 观测',portwatch:'航运观测',news:'公开报道',aircraft:'航空活动'};
  const observationStates = {normal:'正常',attention:'可能异常',warning:'明确异常',unavailable:'参考积累中'};
  const observationColors = {normal:'#72dea2',attention:'#f4d16f',warning:'#ff8e8e',unavailable:'#607b89'};
  function observation() { return state.current?.decision?.primary?.report?.observation; }
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
  function showError() { $('map-loading').hidden=false; $('map-loading').textContent=state.current?'暂时无法更新，正在保留上次观测结论。':'暂时无法取得案例资料，连接恢复后会自动更新。'; }
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
    const decision=state.current?.decision?.primary;
    if(!decision)return null;
    return {title:decision.title,summary:decision.summary,tone:decision.state || 'unavailable',
      evidence:decision.brief_evidence || [],observed:decision.as_of,decision};
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
      const rank={warning:3,attention:2,normal:1,unavailable:0};
      const rows=(observation()?.station_rows || []).filter((row)=>row.station===id);
      const strongest=rows.sort((a,b)=>(rank[b.state] || 0)-(rank[a.state] || 0))[0];
      const color=observation()?observationColors[strongest?.state || 'unavailable']:'#8ae5e0';
      const marker=L.circleMarker(latLng(station),{radius:strongest?.state==='warning'?7:5,color,weight:1.3,fillColor:color,fillOpacity:.8});
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
    if(!data){if(state.popup)state.map.closePopup(state.popup);state.popup=null;$('case-date').textContent=`资料截至 · ${day(state.current?.view.as_of)}`;$('map-loading').hidden=false;$('map-loading').textContent='当前观测资料尚不足以作出监测判定';return;}
    const content=node('div','map-callout'); content.id='map-callout';content.dataset.tone=data.tone;
    const action=button('',()=>openReport('overview'),'callout-main'); action.id='case-conclusion';
    const title={normal:'判定正常，持续监测',attention:'可能出现异常',warning:'明确异常'}[data.tone] || data.title;
    action.append(node('h2','callout-title',title),node('p','callout-summary',data.summary));
    const evidence=node('ul','callout-evidence');data.evidence.forEach((item)=>evidence.append(node('li','',item)));
    action.append(node('span','callout-evidence-label','观测对比'),evidence,node('span','callout-open','查看分析报告 →'));content.append(action);
    if(!state.popup)state.popup=L.popup({className:'case-popup',closeButton:false,closeOnClick:false,autoClose:false,minWidth:240,maxWidth:320,
      autoPanPaddingTopLeft:[12,145],autoPanPaddingBottomRight:[12,100],offset:[18,-9]});
    state.popup.setLatLng(latLng(caseMeta().focus)).setContent(content).openOn(state.map);
    $('case-date').textContent=`观测时间 · ${stamp(data.decision.observation_start || data.observed)}`;
  }
  async function loadCase(recenter=false) {
    const request=++state.request, id=state.caseId;
    const query=state.at?`?at=${encodeURIComponent(state.at)}`:'';
    const view=await get(`/api/replay/cases/${encodeURIComponent(id)}${query}`);
    if(request!==state.request || id!==state.caseId)return;
    $('map-loading').hidden=true;
    const decision=view.decision_snapshot;
    renderObservationControls(decision?.observation_replay);
    if(decision?.observation_replay) {
      const address=new URL(location.href);address.searchParams.set('case',id);
      address.searchParams.set('at',decision.observation_replay.selected_at);history.replaceState(null,'',address);
    }
    const cached=decision?{decision,view:decision.view,metrics:decision.metrics,
      alert:primaryAlert(decision.view.alerts || [])}:{decision:null,view,metrics:null,alert:null};
    const changed=state.current?.decision?.id!==decision?.id || !state.current;
    if(!changed)return;
    if(!recenter && !$('report-panel').hidden && state.current) {
      state.pendingCurrent=cached;
      let update=$('report-update');
      if(!update){update=button('有新的观测结论，点击更新',()=>{
        state.current=state.pendingCurrent;state.pendingCurrent=null;update.remove();
        renderMapFeatures();openReport(state.reportTab,state.station);
      },'evidence-button');update.id='report-update';$('report-panel').insertBefore(update,$('report-body'));}
      return;
    }
    state.current=cached;$('map-loading').hidden=true;renderMapFeatures();
    if(recenter)fitCase();
  }

  async function selectCase(id,at=null) {
    if(!state.metadata?.cases[id])return;
    closeCaseMenu();
    closeReport(); state.request++; state.caseId=id;state.current=null;state.at=at;
    if(state.popup){state.map.closePopup(state.popup);state.popup=null;}
    Object.values(layers).forEach((layer)=>layer.clearLayers());
    document.querySelectorAll('[data-case]').forEach((element)=>{element.classList.toggle('active',element.dataset.case===id);element.setAttribute('aria-pressed',String(element.dataset.case===id));});
    $('map-loading').textContent='正在打开案例…';$('map-loading').hidden=false;
    fitCase();await loadCase(true);
  }
  function renderObservationControls(replay) {
    $('observation-controls').hidden=!replay;
    if(!replay)return;
    const select=$('observation-time');select.replaceChildren();
    for(const item of replay.options) {
      const time=stamp(item.start).replace(' UTC','');
      select.add(new Option(`${time.slice(0,16)} · ${observationStates[item.state] || '待核实'}`,item.time));
    }
    select.value=replay.selected_at;
    const index=replay.options.findIndex((item)=>item.time===replay.selected_at);
    $('observation-prev').disabled=index<=0;
    $('observation-next').disabled=index>=replay.options.length-1;
    $('observation-first').hidden=!replay.first_warning_at;
    $('observation-first').disabled=replay.first_warning_at===replay.selected_at;
  }
  async function selectObservation(at) {
    closeCaseMenu();
    if(!at || at===state.current?.decision?.observation_replay?.selected_at)return;
    closeReport();state.at=at;await loadCase();
  }
  function stepObservation(direction) {
    const replay=state.current?.decision?.observation_replay;
    if(!replay)return;
    const index=replay.options.findIndex((item)=>item.time===replay.selected_at);
    return selectObservation(replay.options[index+direction]?.time);
  }
  function closeCaseMenu() {
    document.body.classList.remove('case-menu-open');$('case-menu-toggle').setAttribute('aria-expanded','false');
  }
  function section(title,parent=$('report-body')) {
    const block=node('section','report-section');block.append(node('h3','',title));parent.append(block);return block;
  }
  function disclosure(title,parent=$('report-body')) {
    const block=node('details','report-section report-disclosure');block.append(node('summary','',title));parent.append(block);return block;
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
      if(id && !seen.has(id)){seen.add(id);parent.append(button(evidenceTitle(record.title) || `引用资料 ${index+1}`,()=>openMaterial(id)));}
      else if(record.url)sourceLink(record.url,record.title,parent);
    });
  }
  function openReport(tab='overview',station=null) {
    if(!state.current)return;state.detailRequest++;state.reportTab=tab;state.station=station;
    $('report-panel').hidden=false;document.body.classList.add('report-open');$('report-back').hidden=true;
    $('report-title').textContent=`${caseName()} · 分析报告`;renderReport();$('report-close').focus();keepCalloutVisible();
  }
  function evidenceTitle(title) {
    return String(title || '').replace(/^station_history(?=\s*·|$)/,'同站历史对照')
      .replace(/^multistation_check(?=\s*·|$)/,'同期多站对照');
  }
  function keepCalloutVisible() {
    if(!state.popup || window.innerWidth<700)return;
    const panel=$('report-panel'),fold=$('layer-fold');
    state.popup.options.autoPanPaddingTopLeft=[fold.open?fold.getBoundingClientRect().right+12:12,145];
    state.popup.options.autoPanPaddingBottomRight=[panel.hidden?12:panel.getBoundingClientRect().width+12,100];
    state.popup.update();
  }
  function closeReport() {
    const opened=!$('report-panel').hidden;state.detailRequest++;$('report-panel').hidden=true;
    document.body.classList.remove('report-open');if(state.pendingCurrent){state.current=state.pendingCurrent;state.pendingCurrent=null;$('report-update')?.remove();renderMapFeatures();}keepCalloutVisible();if(opened)$('case-conclusion')?.focus({preventScroll:true});
  }
  function renderReport() {
    const parent=$('report-body');parent.replaceChildren();parent.scrollTop=0;
    const nav=node('nav','report-tabs');nav.setAttribute('aria-label','报告与证据');
    [['overview','综合研判'],['gnss','GNSS'],['portwatch','航运'],['news','公开资料']].forEach(([id,title])=>{
      const tab=button(title,()=>{state.detailRequest++;state.reportTab=id;state.station=null;renderReport();},state.reportTab===id?'active':'');
      tab.setAttribute('aria-current',state.reportTab===id?'page':'false');nav.append(tab);
    });parent.append(nav);
    if(state.reportTab==='overview')renderOverview();
    else if(state.reportTab==='gnss')renderGnss();
    else if(state.reportTab==='portwatch')renderShipping();
    else renderNews();
  }
  function renderOverview() {
    const data=conclusion(), decision=data?.decision;
    const overview=section('观测结论');
    if(data)overview.append(node('h2','report-conclusion',data.title),node('p','',data.summary));
    else overview.append(node('p','','当前观测资料尚不足以作出监测判定。'));
    overview.append(node('p','report-date',`观测时间：${stamp(decision?.observation_start || data?.observed || state.current.view.as_of)}`));
    if(observation()) {
      renderObservationFacts(observation(),section('指标与历史对比'));
      const links=section('详细数据');
      links.append(button('查看 GNSS 观测',()=>{state.reportTab='gnss';renderReport();}),button('查看航运观测',()=>{state.reportTab='portwatch';renderReport();}));
      const history=disclosure('展开判定时间线');
      const table=node('table','report-table');
      for(const item of state.current.decision.timeline || []) {
        const row=node('tr'),action=node('td');
        action.append(button(observationStates[item.state] || '参考积累中',()=>selectObservation(item.as_of),'text-button'));
        row.append(node('td','',stamp(item.observation_start)),action);table.append(row);
      }
      history.append(table);
      return;
    }
    const evidence=section('观测对比');
    (data?.evidence || []).forEach((text)=>evidence.append(node('p','',text)));
    const assessment=decision?.report?.assessment;
    if(assessment?.statement)disclosure('展开详细分析').append(node('p','',assessment.statement));
    for(const item of Object.values(state.current.decision?.objects || {})) {
      if(item.id===decision?.id)continue;
      const block=section(item.risk_label);block.append(node('p','',item.title),node('p','report-date',item.summary));
    }
    renderInvestigation(decision);
    const sources=node('div','evidence-list');
    [['gnss','查看 GNSS 观测与分析'],['portwatch','查看航运观测与分析'],['news','查看归档公开资料']].forEach(([id,title])=>sources.append(button(title,()=>{state.reportTab=id;state.station=null;renderReport();})));
    evidence.append(sources);
    const timeline=state.current.decision?.timeline || [];
    if(timeline.length){const block=section('判定时间线');const table=node('table','report-table');
      timeline.filter((item)=>item.risk_object===decision?.risk_object).forEach((item)=>{
        const row=node('tr');row.append(node('td','',stamp(item.as_of)),node('td','',item.title));table.append(row);
      });block.append(table);}
    if(decision) {
      const info=disclosure('数据说明');
      info.append(node('p','report-date',decision.risk_object==='gnss_observation_quality'
        ?'数据来自 IGS 接收站。载噪比用于描述卫星信号强弱，以同站、同一时段的历史观测作对比。'
        :'数据来自 PortWatch。通行量按每日船次统计，历史参考采用此前有效日值的中位数。'));
      if(decision.timing?.availability_basis==='simulated_arrival')info.append(node('p','report-date','本页为历史回放，资料到达时间按观测结束时间模拟。'));
    }
  }
  function renderInvestigation(decision) {
    const tools=decision?.investigation?.tools || [];
    const unaddressed=decision?.investigation?.explanation_coverage?.unaddressed || [];
    if(!tools.length && !unaddressed.length)return;
    const block=disclosure('查看补充证据');
    const labels={station_history:'同站历史对照',multistation_check:'同期多站对照',read_material:'原始资料核对',read_gnss:'完整观测核对'};
    tools.forEach((tool)=>{
      block.append(node('strong','',labels[tool.name] || '归档证据核查'));
      if(tool.question)block.append(node('p','',tool.question));
      const result=tool.result?.result || tool.result || {};
      const explanation=result.summary || result.interpretation || result.message;
      if(typeof explanation==='string')block.append(node('p','',explanation));
      const materials=tool.result?.materials || [];
      references(materials,block);
    });
    if(unaddressed.length) {
      const pending=disclosure('分析中的待解问题',block);
      unaddressed.forEach((item)=>pending.append(node('p','report-date',`${item.date?item.date+'：':''}${item.summary}`)));
    }
  }

  function plot(rows,series,parent,unit,timeLabel=day) {
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
    [good[0],good.at(-1)].forEach((row,i)=>chart.append(svg('text',{x:i?width-right:left,y:height-12,'text-anchor':i?'end':'start'},timeLabel(row.time))));
    const wrap=node('div','report-chart');wrap.append(chart);parent.append(wrap);
    const legend=node('div','chart-legend');series.forEach((line)=>{const item=node('span','',line.title);item.style.color=line.color;legend.append(item);});legend.append(node('span','',unit));parent.append(legend);
  }
  function renderObservationFacts(observed,parent,station=null) {
    const table=node('table','report-table'),head=node('tr');
    const isGnss=Array.isArray(observed.station_rows);
    const labels=isGnss?['IGS 站点 / 系统','往日','本次','变化']:['指标','往日参考','本次','较前日'];
    labels.forEach((label)=>head.append(node('th','',label)));table.append(head);
    const rows=isGnss?observed.station_rows.filter((row)=>!station || row.station===station):observed.metrics || [];
    rows.forEach((item)=>{
      const tr=node('tr');tr.dataset.observationState=item.state;
      const label=isGnss?`${item.station} / ${item.system_name}`:item.label;
      const difference=isGnss?item.delta:item.change_pct;
      const change=finite(difference)?`${difference>0?'+':''}${number(difference)} ${isGnss?'dB':'%'}`:'—';
      tr.append(node('td','',label),node('td','',number(item.reference)),node('td','',number(item.current)),node('td','',change));table.append(tr);
    });
    const wrap=node('div','observation-table-wrap');wrap.append(table);parent.append(wrap);
    const details=observed.details || {};
    if(isGnss) {
      const days=[...new Set(rows.map((row)=>row.baseline_days))].sort((a,b)=>a-b);
      parent.append(node('p','report-date',`载噪比单位为 dB-Hz，变化单位为 dB。往日值取各站同一时段的 ${days.join('～')} 天历史观测均值；这里比较同一卫星系统的较弱信号水平。`));
      if(details.baseline_dates?.length)parent.append(node('p','report-date',`参考日期：${details.baseline_dates.join('、')}。`));
    } else {
      parent.append(node('p','report-date','船次按艘次统计；名义运力沿用 PortWatch 原始容量单位，平均运力为总容量除以船次。往日参考为当前观测日前的日值中位数。'));
      const sample=rows.find((row)=>row.reference_days || row.baseline_days);
      const start=sample?.reference_start || details.reference_start || details.baseline_start;
      const end=sample?.reference_end || details.reference_end || details.baseline_end;
      if(start && end)parent.append(node('p','report-date',`参考时段：${start} 至 ${end}。`));
    }
  }
  function renderStationObservations() {
    const observed=observation(),block=section('IGS 接收站：往日与本次');
    const select=node('select','report-select');select.setAttribute('aria-label','选择 IGS 站点');
    select.add(new Option('全部站点',''));
    [...new Set(observed.station_rows.map((row)=>row.station))].sort().forEach((station)=>select.add(new Option(station,station)));
    select.value=state.station || '';block.append(select);
    const detail=node('div');block.append(detail);
    const draw=()=>{
      detail.replaceChildren();renderObservationFacts(observed,detail,select.value || null);
      if(!select.value)return;
      const systems=[...new Set(observed.station_rows.filter((row)=>row.station===select.value).map((row)=>row.system))];
      for(const system of systems) {
        const rows=(observed.history || []).flatMap((frame)=>frame.station_rows
          .filter((row)=>row.station===select.value && row.system===system)
          .map((row)=>({...row,time:frame.start})));
        const name=observed.station_rows.find((row)=>row.system===system)?.system_name || system;
        detail.append(node('h3','',`${name} · 历史观测曲线`));
        plot(rows,[{key:'current',title:'本次载噪比',color:'#73d7d0'},{key:'reference',title:'往日同时段',color:'#bf9f79',dashed:true}],detail,'dB-Hz');
      }
    };
    select.addEventListener('change',draw);draw();
  }
  function renderShippingObservations() {
    const observed=observation(),block=section('航运：船次、运力与船型构成');
    renderObservationFacts(observed,block);
    const chart=section('指标变化'),select=node('select','report-select');select.setAttribute('aria-label','选择航运指标');
    observed.metrics.forEach((metric)=>select.add(new Option(metric.label,metric.key)));
    select.value=observed.metrics.some((item)=>item.key==='capacity')?'capacity':observed.metrics[0]?.key;
    chart.append(select);const drawing=node('div');chart.append(drawing);
    const draw=()=>{
      drawing.replaceChildren();const metric=observed.metrics.find((item)=>item.key===select.value);
      const rows=(observed.history || []).map((frame)=>{
        const row=frame.metrics.find((item)=>item.key===select.value) || {};
        return {time:frame.start,current:row.current,reference:row.reference};
      });
      plot(rows,[{key:'current',title:metric.label,color:'#dbae78'},{key:'reference',title:'此前日值中位数',color:'#6aaab2',dashed:true}],drawing,metric.unit || '原始单位');
    };
    select.addEventListener('change',draw);draw();
    const history=disclosure('展开日度原始数值');
    const table=node('table','report-table'),head=node('tr');
    ['观测日期','总船次','名义总运力','油轮名义运力'].forEach((text)=>head.append(node('th','',text)));table.append(head);
    for(const frame of observed.history || []) {
      const values=Object.fromEntries(frame.metrics.map((row)=>[row.key,row.current]));
      const row=node('tr');row.append(node('td','',day(frame.start)),node('td','',number(values.n_total)),node('td','',number(values.capacity)),node('td','',number(values.capacity_tanker)));table.append(row);
    }
    const wrap=node('div','observation-table-wrap');wrap.append(table);history.append(wrap);
  }
  function renderSavedGnss() {
    const block=section('IGS 接收站：往日与本次');
    const mainDate=day(state.current?.decision?.primary?.observation_start || state.current?.decision?.primary?.as_of);
    const key=`${state.caseId}:${mainDate}`, caseId=state.caseId;
    if(state.gnssArchive?.key!==key)state.gnssArchive={key,data:null};
    const clock=(value)=>String(value || '').slice(11,16);
    const sourceRows=(data,station,signal)=>(data.series || [])
      .filter((row)=>row.station===station && (!signal || row.signal===signal))
      .sort((a,b)=>String(a.window_start).localeCompare(String(b.window_start)));
    const value=(row)=>row?.unit==='dB-Hz'?row.cnr_p10:row?.strength_p10;
    const unit=(row)=>row?.unit==='dB-Hz'?'dB-Hz':'接收机原始单位';
    const signalsFor=(data,station)=>[...new Set([
      ...sourceRows(data,station).map((row)=>row.signal),
      ...(data.files || []).filter((file)=>file.station===station).flatMap((file)=>(file.signals || []).map((item)=>item.signal))
    ])].filter(Boolean).sort();
    const preferredSignal=(signals,station)=>['BSHM00ISR','KITG00UZB'].includes(station) && signals.includes('C:S1P')?'C:S1P':signals[0];
    const representative=(rows)=>rows.find((row)=>finite(value(row)) && Date.parse(row.window_end)-Date.parse(row.window_start)>=300000)
      || rows.find((row)=>finite(value(row))) || rows[0];
    function renderSignal(data,station,signal,parent) {
      const rows=sourceRows(data,station,signal),sample=representative(rows);
      parent.append(node('h3','',`${station} · ${signal}`));
      if(rows.length) {
        const groups=[...new Set(rows.flatMap((row)=>(row.reference_groups || []).map((group)=>group.date_group)))];
        const drawing=rows.map((row)=>({time:row.window_start,current:value(row),
          ...Object.fromEntries((row.reference_groups || []).map((group)=>[`ref_${group.date_group}`,group.sample_median]))}));
        const references=groups.map((name,index)=>{
          const dates=[...new Set(rows.flatMap((row)=>(row.reference_groups || []).filter((group)=>group.date_group===name).flatMap((group)=>group.dates || [])))];
          return {key:`ref_${name}`,title:`往日 ${dates.join('、') || name}`,color:index?'#9ea9dc':'#bf9f79',dashed:true};
        });
        plot(drawing,[{key:'current',title:`本次 ${data.selected_date}`,color:'#73d7d0'},...references],parent,unit(sample),clock);
        parent.append(node('p','report-date',`观测时间 ${data.selected_date} ${clock(rows[0].window_start)}–${clock(rows.at(-1).window_end)} UTC。${sample?.unit==='dB-Hz'?'载噪比（信号偏弱部分）。':'信号强度使用接收机原始单位。'}`));
        const numbers=disclosure('展开各时段数值',parent),table=node('table','report-table gnss-window-table'),head=node('tr');
        ['时间（UTC）','往日','本次','变化'].forEach((label)=>head.append(node('th','',label)));table.append(head);
        for(const row of rows) {
          const groups=row.reference_groups?.length?row.reference_groups:[null];
          for(const group of groups) {
            const current=finite(group?.current_p10)?group.current_p10:value(row),reference=group?.sample_median;
            const delta=finite(current) && finite(reference)?current-reference:null;
            const tr=node('tr');tr.append(node('td','',`${clock(row.window_start)}–${clock(row.window_end)}`),
              node('td','',number(reference)),node('td','',number(current)),node('td','',finite(delta)?`${delta>0?'+':''}${number(delta)}`:'—'));table.append(tr);
          }
        }
        numbers.append(table,node('p','report-date',`数值单位：${unit(sample)}；变化为同一时段本次值减去往日值。`));
      } else parent.append(node('p','report-empty','这条信号只有文件汇总，可打开原始资料查看。'));
      const files=(data.files || []).filter((file)=>file.station===station && (file.signals || []).some((item)=>item.signal===signal));
      const seen=new Set();
      for(const file of files)if(file.material_id && !seen.has(file.material_id)) {
        seen.add(file.material_id);parent.append(button(`原始资料 · ${file.source_filename || `${station} ${clock(file.window_start)}`}`,()=>openMaterial(file.material_id)));
      }
    }
    function draw(data) {
      block.replaceChildren(node('h3','','IGS 接收站：往日与本次'));
      const control=node('div','gnss-date-controls'),select=node('select','report-select');
      select.setAttribute('aria-label','选择 GNSS 观测日期');
      (data.dates || []).forEach((date)=>select.add(new Option(date,date)));select.value=data.selected_date;
      select.addEventListener('change',()=>load(select.value));
      const index=(data.dates || []).indexOf(data.selected_date);
      const previous=button('前一天',()=>load(data.dates[index-1]),'text-button'),next=button('后一天',()=>load(data.dates[index+1]),'text-button');
      previous.disabled=index<=0;next.disabled=index<0 || index>=data.dates.length-1;
      control.append(previous,select,next);block.append(node('label','report-label','GNSS 观测日期'),control);
      block.append(node('p','report-date',`GNSS 观测日期：${data.selected_date}；主卡航运日期：${mainDate}。`));
      if(!data.series?.length && !data.files?.length){block.append(node('p','report-empty','这一天尚无接收站观测资料。'));return;}
      block.append(node('p','report-date','点击站点查看全部信号、往日对照曲线和原始资料。'));
      const stations=[...new Set([...(data.stations || []),...(data.files || []).map((file)=>file.station),...(data.series || []).map((row)=>row.station)])].sort();
      const table=node('table','report-table gnss-station-table'),head=node('tr'),detailContainer=node('div');
      ['站点 / 信号','往日','本次','变化'].forEach((label)=>head.append(node('th','',label)));table.append(head);
      for(const station of stations) {
        const signals=signalsFor(data,station),signal=preferredSignal(signals,station),rows=sourceRows(data,station,signal),sample=representative(rows);
        const group=(sample?.reference_groups || []).find((item)=>finite(item.sample_median));
        const current=finite(group?.current_p10)?group.current_p10:value(sample),reference=group?.sample_median;
        const delta=finite(current) && finite(reference)?current-reference:null;
        const details=disclosure(`${station} · 信号曲线与原始资料`,detailContainer);
        const choices=node('select','report-select');choices.setAttribute('aria-label',`${station} 的卫星信号`);
        choices.add(new Option('全部信号',''));signals.forEach((item)=>choices.add(new Option(item,item)));choices.value=signal || '';
        const charts=node('div');details.append(choices,charts);
        const drawSignals=()=>{charts.replaceChildren();(choices.value?[choices.value]:signals).forEach((item)=>renderSignal(data,station,item,charts));};
        choices.addEventListener('change',drawSignals);
        details.addEventListener('toggle',()=>{if(details.open && !charts.childNodes.length)drawSignals();});
        if(state.station===station)details.open=true;
        const tr=node('tr'),label=node('td'),past=node('td','',number(reference)),now=node('td','',number(current)),change=node('td','',finite(delta)?`${delta>0?'+':''}${number(delta)}`:'—');
        label.append(button(station,()=>{details.open=true;drawSignals();details.scrollIntoView({block:'nearest',behavior:'smooth'});},'text-button'),node('small','',signal || '信号未注明'));
        if(sample)label.append(node('small','',`${clock(sample.window_start)}–${clock(sample.window_end)} UTC`));
        if(group?.outside_sample_range===false)label.append(node('small','gnss-near-reference','与往日接近'));
        else if(finite(current) && finite(group?.sample_min) && current<group.sample_min)label.append(node('small','gnss-below-reference','低于往日'));
        past.append(node('small','',(group?.dates || []).join('、') || '暂无同期参考'));
        now.append(node('small','',unit(sample)));if(finite(delta))change.append(node('small','',sample?.unit==='dB-Hz'?'dB':'接收机原始单位'));
        if(sample?.unit==='receiver_units')now.append(node('small','','原件未标物理单位'));
        tr.append(label,past,now,change);table.append(tr);
      }
      const wrap=node('div','observation-table-wrap');wrap.append(table);block.append(wrap);
      block.append(node('p','report-date','载噪比（信号偏弱部分）：往日与本次使用同站、同信号、同一时段。表内列出当日首个完整五分钟的观测。'));
      if(data.reference_dates?.length)block.append(node('p','report-date',`参考日期：${data.reference_dates.join('、')}。`));
      block.append(detailContainer);
    }
    async function load(date=null) {
      const request=++state.gnssRequest;
      block.replaceChildren(node('p','report-empty','正在读取 GNSS 观测…'));
      try {
        let data=await get(`/api/replay/cases/${encodeURIComponent(caseId)}/gnss-observations${date?`?date=${encodeURIComponent(date)}`:''}`);
        const preferredDate=state.gnssInitialDate || mainDate;
        if(!date && data.dates?.includes(preferredDate) && data.selected_date!==preferredDate)data=await get(`/api/replay/cases/${encodeURIComponent(caseId)}/gnss-observations?date=${encodeURIComponent(preferredDate)}`);
        if(request!==state.gnssRequest || !block.isConnected || state.caseId!==caseId)return;
        state.gnssInitialDate=null;state.gnssArchive={key,data};draw(data);
      } catch {
        if(request!==state.gnssRequest || !block.isConnected)return;
        block.replaceChildren(node('p','report-empty','GNSS 观测暂时无法读取。'),button('重新读取',()=>load(date)));
      }
    }
    if(state.gnssArchive.data)draw(state.gnssArchive.data);else load();
  }
  function renderGnss() {
    if(state.caseId==='hormuz') {renderSavedGnss();return;}
    if(observation()?.station_rows) {
      renderStationObservations();return;
    }
    const block=section('接收站观测与参考');
    const files=(state.current.view.gnss?.summary || []).sort((a,b)=>String(a.window_start).localeCompare(String(b.window_start)));
    const keys=[...new Set(files.flatMap((file)=>(file.signals || []).map((signal)=>`${file.station} / ${signal.signal}`)))];
    if(!keys.length){block.append(node('p','report-empty','当前快照尚无可展示的接收站统计。'));return;}
    const select=node('select','report-select');select.setAttribute('aria-label','选择观测站和信号');
    keys.forEach((key)=>select.add(new Option(key,key)));
    if(state.station)select.value=keys.find((key)=>key.startsWith(`${state.station} / `)) || keys[0];
    block.append(node('label','report-label','观测站与卫星信号'),select);
    const detail=node('div');block.append(detail);
    const draw=()=>{
      detail.replaceChildren();const [station,signal]=select.value.split(' / ');
      const records=files.filter((file)=>file.station===station).flatMap((file)=>(file.signals || []).filter((item)=>item.signal===signal).map((item)=>({...item,file})));
      const latest=records.at(-1),cnr=latest?.unit==='dB-Hz';
      metrics([['信号质量',cnr?`${number(latest?.cnr_p10)} dB-Hz`:`${number(latest?.strength_p10)} 接收机单位`],
        ['有效信号样本',number(latest?.valid_count)],['可比窗口',number(latest?.matched_window_count)]],detail);
      detail.append(node('p','report-date',cnr?'载噪比（CNR）越低，卫星信号越弱。此处显示整份观测文件的低分位值（P10）。':'本文件使用接收机自身的信号单位，未提供 dB-Hz 换算。'));
      const groups=latest?.reference_groups || [];
      if(groups.length){
        const table=node('table','report-table'),head=node('tr');['参考日期','窗口差值中位数','可比窗口'].forEach((text)=>head.append(node('th','',text)));table.append(head);
        groups.forEach((group)=>{const tr=node('tr');tr.append(node('td','',(group.dates || []).join('、')),
          node('td','',`${number(group.window_difference_median)} dB`),node('td','',number(group.matched_window_count)));table.append(tr);});detail.append(table);
        detail.append(node('p','report-date','按同站、同信号、同一时段比较。表格显示各窗口差值的中位数，曲线显示每五分钟的观测。'));
      } else detail.append(node('p','report-date','本信号当前没有满足单位和窗口条件的参考比较。'));
      const windowRows=gnssRows(station).filter((row)=>row.signal===signal);
      if(windowRows.length) {
        const groupNames=[...new Set(windowRows.flatMap((row)=>(row.reference_groups || []).map((group)=>group.date_group)))];
        const plotRows=windowRows.map((row)=>({...row,...Object.fromEntries((row.reference_groups || []).map((group)=>[`ref_${group.date_group}`,group.sample_median]))}));
        plot(plotRows,[{key:'p10',title:'五分钟信号质量',color:'#73d7d0'},...groupNames.map((group,i)=>({key:`ref_${group}`,title:`${group} 参考`,color:i?'#9ea9dc':'#bf9f79',dashed:true}))],detail,'dB-Hz');
      } else if(cnr) {
        plot(records.map((item)=>({time:item.file.window_start,p10:item.cnr_p10})),[{key:'p10',title:'每份文件的信号低分位',color:'#73d7d0'}],detail,'dB-Hz');
      }
      detail.append(node('p','report-date',`本次文件实际覆盖：${stamp(latest?.file.window_start)} 至 ${stamp(latest?.file.window_end)}。`));
    };
    select.addEventListener('change',draw);draw();
    const decision=state.current.decision?.objects?.gnss_observation_quality;
    renderInvestigation(decision);renderEnvironment(decision);renderWorkList('gnss');
  }
  function renderEnvironment(decision) {
    const context=decision?.report?.program?.environment_context;
    if(!context?.rows?.length)return;
    const block=section('归档环境参考');
    block.append(node('p','report-date',context.scope_note));
    context.rows.forEach((row)=>{
      block.append(node('strong','',`${row.date} · ${row.relation}`));
      const table=node('table','report-table');
      [['Kp_3hour','Kp（三小时序列）'],['ap_3hour','ap（三小时序列）'],['Ap','Ap（日值）'],
        ['F10_7_observed','太阳射电通量（观测值）'],['F10_7_adjusted','太阳射电通量（调整值）']].forEach(([key,label])=>{
        if(row[key]==null)return;
        const tr=node('tr'),value=Array.isArray(row[key])?row[key].map((item)=>number(item)).join(' · '):number(row[key]);
        tr.append(node('th','',label),node('td','',value));table.append(tr);
      });block.append(table);
      references([{id:row.material_id,title:`GFZ ${row.date} 原始环境资料`}],block);
    });
    if(context.units)block.append(node('p','report-date',`原件单位：${typeof context.units==='string'?context.units:Object.entries(context.units).map(([key,value])=>`${key}：${value}`).join('；')}`));
    if(context.time_system)block.append(node('p','report-date',`资料时间：${context.time_system}`));
    block.append(node('p','report-date',context.availability_note));
  }
  function renderShipping() {
    if(observation()?.metrics) {
      renderShippingObservations();return;
    }
    const data=state.current.metrics,alert=state.current.alert?.origin_type==='numeric_rule'?state.current.alert:null;
    const block=section('航运观测证据');
    if(!data?.observations?.length){block.append(node('p','report-empty','本例未收录航运观测。'));renderWorkList('portwatch');return;}
    const latest=data.state,baseline=alert?.baseline ?? latest?.baseline;
    metrics([['最新通行量',number(latest?.latest_value)],['历史参考',number(baseline)],['相对参考',finite(latest?.ratio)?`${number(latest.ratio*100)}%`:'—']],block);
    const rows=data.observations.map((row)=>({...row,time:row.observed_date,baseline})).sort((a,b)=>a.time.localeCompare(b.time));
    plot(rows,[{key:'n_total',title:'可见通行量',color:'#dbae78'},{key:'baseline',title:'历史参考',color:'#6aaab2',dashed:true}],block,'船／航次');
    block.append(node('p','report-date','PortWatch 海峡日度汇总。地图标记表示观测区域，数值单位为船／航次。'));
    const decision=state.current.decision?.objects?.maritime_visible_flow;
    const composition=decision?.report?.program?.flow_context;
    if(composition?.changes) {
      const detail=section('船流构成与名义运力');
      detail.append(node('p','report-date',`${composition.previous?.observed_date || '前日无资料'} → ${composition.latest?.observed_date || '本日'}；名义运力是船舶容量指标，不代表实际货物装载量。`));
      const labels={n_total:'总船次',n_tanker:'油轮船次',n_cargo:'货船船次',capacity:'名义总运力',capacity_tanker:'油轮名义运力',capacity_cargo:'货船名义运力',capacity_per_ship:'每船次名义运力',tanker_capacity_per_tanker:'每油轮名义运力'};
      const table=node('table','report-table'),head=node('tr');['指标','前日','本日','变化'].forEach((text)=>head.append(node('th','',text)));table.append(head);
      Object.entries(labels).forEach(([key,label])=>{const item=composition.changes[key] || {},tr=node('tr');
        tr.append(node('td','',label),node('td','',number(item.previous)),node('td','',number(item.current)),node('td','',finite(item.change_pct)?`${number(item.change_pct)}%`:'不可计算'));table.append(tr);});detail.append(table);
    }
    renderInvestigation(decision);
    if(alert){const trigger=section('异常判据');trigger.append(node('p','',`连续 ${alert.rule.trigger_days} 天低于历史参考的 ${number(alert.rule.trigger_ratio*100)}%。参考时段为 ${alert.baseline_start} 至 ${alert.baseline_end}。`));
      (alert.trigger_observations || []).forEach((row)=>trigger.append(button(`${row.observed_date} · ${row.n_total} 船／航次 · 查看依据`,()=>openMaterial(row.material_id))));}
    const daily=section('日度数据');const table=node('table','report-table'),head=node('tr');['日期','通行量','原始资料'].forEach((title)=>head.append(node('th','',title)));table.append(head);
    [...rows].reverse().forEach((row)=>{const tr=node('tr'),link=node('td');if(row.material_id)link.append(button('查看',()=>openMaterial(row.material_id),'text-button'));tr.append(node('td','',row.observed_date),node('td','',number(row.n_total)),link);table.append(tr);});daily.append(table);renderWorkList('portwatch');
  }
  function renderNews() {
    const materials=Object.values(state.current.decision?.objects || {}).flatMap((item)=>item.report?.program?.archive_index || []);
    const block=section('按当前时间可见的归档资料');
    if(materials.length)references(materials,block);
    else block.append(node('p','report-empty','当前判定没有引用可展开的环境观测或历史参考归档。'));
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
      block.append(node('span','report-status',decisions[assessment.decision] || '分析结论'),node('h2','report-conclusion',assessment.title || '观测与研判'),node('p','',assessment.statement || '当前资料未形成分析结论。'),node('p','report-date',`可见资料截至 ${stamp(work.as_of)}`));
      const limits=Array.isArray(assessment.limitations)?assessment.limitations:assessment.limitations?[assessment.limitations]:[];
      if(limits.length){const limitsBlock=disclosure('查看分析补充说明');limits.forEach((text)=>limitsBlock.append(node('p','',text)));}
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
      block.append(node('h2','report-conclusion',evidenceTitle(material.title) || '原始观测资料'),node('p','report-date',`资料时间：${stamp(material.observed_at || material.available_at)}`));
      sourceLink(material.url,'打开原始来源',block);
      if(material.analysis?.summary)section('资料摘要').append(node('p','',typeof material.analysis.summary==='string'?material.analysis.summary:JSON.stringify(material.analysis.summary)));
      let structured=null;try{structured=JSON.parse(material.text);}catch{}
      if(structured && typeof structured==='object') {
        const details=section('观测与比较');
        renderStructuredEvidence(structured,details);
      } else section('来源内容').append(node('p','source-excerpt',material.text || '该资料为结构化观测，数值与曲线见对应证据页。'));
    });
  }
  function renderStructuredEvidence(data,parent) {
    const labels={station:'接收站',signal:'卫星信号',unit:'单位',units:'单位',as_of:'可见截止',
      window_start:'窗口开始',window_end:'窗口结束',current_window:'本次观测',
      reference_groups:'按日期分组的参考',reference_dates:'参考日期',reference_days:'参考天数',
      p10:'信号质量低分位',cnr_p10:'CNR 低分位',strength_p10:'接收机原始信号值',
      sample_count:'有效样本',samples:'有效样本',epoch_count:'有效历元',
      descriptive_departures:'历史样本比较',limitations:'适用限制',summary:'摘要',
      result:'核查结果',groups:'比较组',comparisons:'窗口对照',stations:'接收站',
      observed_date:'观测日',n_total:'总通行量',n_tanker:'油轮通行量',n_cargo:'货船通行量',
      capacity:'名义总运力',capacity_tanker:'油轮名义运力',capacity_cargo:'货船名义运力',
      interpretation:'解释',reason:'原因',spatial_limit:'空间范围',time_note:'时间说明'};
    Object.assign(labels,{current_windows:'当前窗口',reference_windows:'参考窗口',date_group:'参考月份',dates:'参考日期',
      reference_day_count:'参考天数',matched_window_count:'可比窗口数',current_p10:'当前信号低分位',
      sample_min:'历史样本下界',sample_median:'历史样本中位数',sample_max:'历史样本上界',
      window_difference_median:'窗口差值中位数',current_window_p10_median:'当前窗口低分位中位数',
      reference_window_medians_median:'参考窗口中位数汇总',valid_count:'有效信号样本',valid_ratio:'有效比例',
      epoch_coverage_ratio:'历元覆盖比例',missing_epoch_count:'缺失历元',interval_seconds:'采样间隔（秒）',
      station_signal_summaries:'各站可比支持',unavailable_or_incomparable_stations:'未参与同期比较的站点',
      reasons:'原因',concurrent_windows:'真实同期窗口',overlap_window_count:'同期窗口数',
      coverage:'覆盖范围',current_windows_count:'当前窗口数',valid_cnr_windows:'有效 CNR 窗口',
      matched_current_windows:'有参考的当前窗口',unit_limited_windows:'单位受限窗口',
      comparability:'比较条件',selection_note:'资料选择依据',sampling_note:'展示范围'});
    let shown=false;
    Object.entries(data).forEach(([key,value])=>{
      if(!(key in labels) || value==null)return;shown=true;
      if(Array.isArray(value)) {
        const details=node('details');details.append(node('summary','',`${labels[key]}（${value.length}）`));
        value.forEach((item)=>{if(item && typeof item==='object')renderStructuredEvidence(item,details);else details.append(node('p','',String(item)));});parent.append(details);
      } else if(typeof value==='object'){const details=node('details');details.append(node('summary','',labels[key]));renderStructuredEvidence(value,details);parent.append(details);}
      else parent.append(node('p','',`${labels[key]}：${value}`));
    });
    if(!shown)parent.append(node('p','report-date','完整数值已保留在该来源资料中，相关信号和历史窗口可在证据页查看。'));
  }
  function openAlert(id) {
    return detailView('区域异常分析',`/api/alerts/${encodeURIComponent(id)}`,(alert)=>{
      const block=section('异常结论');block.append(node('h2','report-conclusion',alert.title || '区域异常'),node('p','',alert.analysis?.statement || alert.summary || alert.statement));
      if(alert.evidence_refs?.length)references(alert.evidence_refs,section('支持资料'));
    });
  }
  async function refresh() {
    if(document.hidden || state.busy || !state.metadata)return;state.busy=true;
    try{await loadCase();}catch(error){showError(error);}finally{state.busy=false;}
  }
  async function start() {
    const [metadata,world]=await Promise.all([get('/static/replay-map-data.json'),get('/static/replay-world.geojson')]);
    state.metadata=metadata;initializeMap(world);
    document.querySelectorAll('[data-case]').forEach((element)=>element.addEventListener('click',()=>selectCase(element.dataset.case).catch(showError)));
    ['gnss','aircraft','shipping'].forEach((kind)=>$(`layer-${kind}`).addEventListener('change',()=>{state.layerChoice[kind]=$(`layer-${kind}`).checked;toggleLayer(kind);}));
    $('map-home-btn').addEventListener('click',fitCase);$('report-close').addEventListener('click',closeReport);
    $('case-menu-toggle').addEventListener('click',()=>{
      const opened=document.body.classList.toggle('case-menu-open');$('case-menu-toggle').setAttribute('aria-expanded',String(opened));
    });
    $('observation-time').addEventListener('change',(event)=>selectObservation(event.target.value).catch(showError));
    $('observation-prev').addEventListener('click',()=>Promise.resolve(stepObservation(-1)).catch(showError));
    $('observation-next').addEventListener('click',()=>Promise.resolve(stepObservation(1)).catch(showError));
    $('observation-first').addEventListener('click',()=>selectObservation(state.current?.decision?.observation_replay?.first_warning_at).catch(showError));
    $('report-back').addEventListener('click',()=>openReport(state.reportTab,state.station));
    document.addEventListener('keydown',(event)=>{if(event.key==='Escape'){closeReport();closeCaseMenu();}});
    document.addEventListener('visibilitychange',refresh);
    let resizeTimer;
    window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{state.map.invalidateSize();fitCase();},120);});
    if(window.innerWidth<700)$('layer-fold').open=false;
    const parameters=new URLSearchParams(location.search),initial=parameters.get('case');
    await selectCase(state.metadata.cases[initial]?initial:'kharkiv',parameters.get('at'));
    if(state.caseId==='hormuz' && parameters.get('report')==='gnss') {
      state.gnssInitialDate=parameters.get('gnss_date');openReport('gnss');
    }
    setInterval(refresh,15000);
  }
  start().catch(showError);
})();

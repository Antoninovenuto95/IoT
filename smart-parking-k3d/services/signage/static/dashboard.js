const metrics = Array.from(document.querySelectorAll('.card[data-metric]')).reduce((acc, el)=>{
  acc[el.dataset.metric] = el.querySelector('.value');
  return acc;
}, {});
const lotsEl = document.getElementById('lots');
const spacesEl = document.getElementById('spaces');
const lastUpdateEl = document.getElementById('last-update');

function cls(free, total){
  if (total===0) return 'warn';
  const ratio = free/total;
  if (ratio>0.5) return 'ok';
  if (ratio>0.2) return 'warn';
  return 'bad';
}

function formatDate(value){
  if(!value) return '—';
  try {
    const d = new Date(value);
    if(!isNaN(d.getTime())){
      return d.toLocaleString();
    }
  } catch(e) {}
  return value;
}

function renderSummary(summary){
  Object.entries(summary).forEach(([key,val])=>{
    if(metrics[key]){
      metrics[key].textContent = typeof val === 'number' ? val : (val ?? '—');
    }
  });
}

function renderLots(lots){
  if(!lots || !lots.length){
    lotsEl.innerHTML = '<div class="empty">Nessun parcheggio disponibile</div>';
    return;
  }
  lotsEl.innerHTML = lots.map(l=>{
    const c = cls(l.free, l.totalSpaces);
    return `
      <div class="lot-card">
        <div class="lot-id">PARCHEGGIO ${l.lotId}</div>
        <div class="lot-free ${c}">${l.free}</div>
        <div class="lot-meta">Liberi su ${l.totalSpaces}</div>
        <div class="lot-meta">Ultimo aggiornamento: ${l.lastUpdate ? formatDate(l.lastUpdate) : '—'}</div>
      </div>`;
  }).join('');
}

function renderSpaces(spaces){
  if(!spaces || !spaces.length){
    spacesEl.innerHTML = '<tr><td colspan="5" class="empty">Nessuno stallo rilevato</td></tr>';
    return;
  }
  spacesEl.innerHTML = spaces.map(s=>{
    const occCls = s.occupied ? 'bad' : 'ok';
    const occLabel = s.occupied ? 'Occupato' : 'Libero';
    const sensorCls = s.sensorOnline ? 'online' : 'offline';
    const sensorLabel = s.sensorOnline ? 'Online' : 'Offline';
    return `
      <tr>
        <td>${s.lotId || '—'}</td>
        <td>${s.spaceId || '—'}</td>
        <td><span class="status-pill ${occCls}">${occLabel}</span></td>
        <td><span class="status-pill ${sensorCls}">${sensorLabel}</span></td>
        <td>${formatDate(s.lastSeen)}</td>
      </tr>`;
  }).join('');
}

function render(data){
  if(!data){
    lotsEl.innerHTML = '<div class="empty">Errore nel caricamento…</div>';
    spacesEl.innerHTML = '<tr><td colspan="5" class="empty">Errore nel caricamento…</td></tr>';
    return;
  }
  renderSummary(data.summary || {});
  renderLots(data.lots || []);
  renderSpaces(data.spaces || []);
  lastUpdateEl.textContent = new Date().toLocaleTimeString();
}

async function tick(){
  try{
    const r = await fetch('/dashboard-data', {cache:'no-store'});
    if(!r.ok){ throw new Error('HTTP '+r.status); }
    const data = await r.json();
    render(data);
  }catch(e){
    render(null);
  }
}

tick();
setInterval(tick, 3000);
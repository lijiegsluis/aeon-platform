// Advanced features for platform.html - inject after DATA loads

// === COMPARATIVE ANALYSIS ===
window.compareCompanies = function(slugs) {
  if (!slugs || slugs.length < 2) return alert('Select 2-5 companies to compare');

  fetch(`/api/compare/?slugs=${slugs.join(',')}`)
    .then(r => r.json())
    .then(data => {
      const html = `
        <div style="background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:20px;margin:20px 0">
          <h2 style="margin-top:0">Comparative Analysis</h2>
          <table class="port-tbl">
            <thead><tr>
              <th>Company</th>
              <th class="rt">Mkt Cap</th>
              <th class="rt">EV/EBITDA</th>
              <th class="rt">FCF Yield</th>
              <th class="rt">Rev CAGR</th>
              <th class="rt">Margin</th>
              <th class="rt">Rating</th>
              <th class="rt">Upside</th>
            </tr></thead>
            <tbody>
              ${data.companies.map(c => `
                <tr onclick="showCo('${c.slug}')" style="cursor:pointer">
                  <td><strong>${c.name}</strong><br><small style="color:var(--muted)">${c.ticker}</small></td>
                  <td class="rt tnum">${c.market_cap_m ? '$' + Math.round(c.market_cap_m) + 'M' : '—'}</td>
                  <td class="rt tnum">${c.ev_ebitda ? c.ev_ebitda.toFixed(1) + 'x' : '—'}</td>
                  <td class="rt tnum">${c.fcf_yield ? (c.fcf_yield * 100).toFixed(1) + '%' : '—'}</td>
                  <td class="rt tnum">${c.revenue_cagr_3y ? (c.revenue_cagr_3y * 100).toFixed(1) + '%' : '—'}</td>
                  <td class="rt tnum">${c.ebitda_margin_latest ? (c.ebitda_margin_latest * 100).toFixed(1) + '%' : '—'}</td>
                  <td class="rt"><span class="rt rt-${c.rating || 'NR'}">${c.rating || 'NR'}</span></td>
                  <td class="rt tnum" style="color:${c.upside > 0 ? 'var(--buy)' : 'var(--reduce)'}">${c.upside ? c.upside.toFixed(0) + '%' : '—'}</td>
                </tr>
              `).join('')}
              ${data.sector_medians.ev_ebitda ? `
                <tr style="border-top:2px solid var(--line);font-weight:700;background:var(--soft)">
                  <td>Sector Median</td>
                  <td class="rt">—</td>
                  <td class="rt tnum">${data.sector_medians.ev_ebitda.toFixed(1)}x</td>
                  <td class="rt tnum">${data.sector_medians.fcf_yield ? (data.sector_medians.fcf_yield * 100).toFixed(1) + '%' : '—'}</td>
                  <td class="rt">—</td>
                  <td class="rt">—</td>
                  <td class="rt">—</td>
                  <td class="rt">—</td>
                </tr>
              ` : ''}
            </tbody>
          </table>
          <button class="btn" onclick="this.parentElement.remove()">Close</button>
        </div>
      `;
      document.getElementById('main').insertAdjacentHTML('afterbegin', html);
    });
};

// === ADVANCED SCREENER ===
window.runScreener = function(type) {
  const params = new URLSearchParams();

  if (type === 'undervalued') {
    params.set('max_ev_ebitda', '12');
    params.set('min_fcf_yield', '0.05');
    params.set('rating', 'buy');
  } else if (type === 'growth') {
    params.set('min_revenue_cagr', '0.15');
    params.set('margin_expansion', 'true');
  }

  const endpoint = type === 'growth' ? '/api/screener/growth' : '/api/screener/valuation';

  fetch(`${endpoint}?${params}`)
    .then(r => r.json())
    .then(data => {
      const html = `
        <div style="background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:20px;margin:20px 0">
          <h2 style="margin-top:0">${type === 'undervalued' ? 'Undervalued Companies' : 'High Growth Companies'} (${data.count})</h2>
          <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px">
            ${data.matches.slice(0, 12).map(c => `
              <div onclick="showCo('${c.slug}')" style="background:var(--soft);border:1px solid var(--line);border-radius:6px;padding:14px;cursor:pointer">
                <div style="font-weight:700;font-size:14px;color:var(--navy);margin-bottom:4px">${c.name}</div>
                <div style="font-size:12px;color:var(--muted);margin-bottom:8px">${c.ticker} · ${c.sector}</div>
                ${c.ev_ebitda ? `<div style="font-size:12px">EV/EBITDA: <strong>${c.ev_ebitda.toFixed(1)}x</strong></div>` : ''}
                ${c.fcf_yield ? `<div style="font-size:12px">FCF Yield: <strong>${(c.fcf_yield * 100).toFixed(1)}%</strong></div>` : ''}
                ${c.revenue_cagr ? `<div style="font-size:12px">Rev CAGR: <strong>${(c.revenue_cagr * 100).toFixed(1)}%</strong></div>` : ''}
                ${c.upside ? `<div style="font-size:12px;margin-top:6px;color:var(--buy)">Upside: <strong>${c.upside.toFixed(0)}%</strong></div>` : ''}
              </div>
            `).join('')}
          </div>
          <button class="btn" onclick="this.parentElement.remove()" style="margin-top:16px">Close</button>
        </div>
      `;
      document.getElementById('main').insertAdjacentHTML('afterbegin', html);
    });
};

// === WATCHLIST INTEGRATION ===
window.loadWatchlists = function() {
  fetch('/api/watchlist/')
    .then(r => r.json())
    .then(wls => {
      window.userWatchlists = wls;
      console.log('Loaded', wls.length, 'watchlists');
    });
};

window.addToWatchlist = function(slug) {
  if (!window.userWatchlists || window.userWatchlists.length === 0) {
    const name = prompt('Create watchlist:');
    if (!name) return;

    fetch('/api/watchlist/', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name, slugs: [slug]})
    }).then(() => {
      alert('Watchlist created');
      loadWatchlists();
    });
  } else {
    const wl = window.userWatchlists[0];
    fetch(`/api/watchlist/${wl.id}/add/${slug}`, {method: 'PUT'})
      .then(() => alert('Added to watchlist'));
  }
};

// === SIDEBAR ENHANCEMENTS ===
window.enhanceSidebar = function() {
  const sideHead = document.querySelector('.side-head');
  if (!sideHead) return;

  // Add valuation filter
  const filterRow = document.createElement('div');
  filterRow.className = 'side-row';
  filterRow.style.marginTop = '6px';
  filterRow.innerHTML = `
    <select id="valuationFilter" onchange="filterChanged()">
      <option value="">All valuations</option>
      <option value="cheap">Cheap (EV/EBITDA < 10x)</option>
      <option value="fair">Fair (10-15x)</option>
      <option value="expensive">Expensive (> 15x)</option>
    </select>
  `;
  sideHead.appendChild(filterRow);

  // Add screener shortcuts
  const shortcuts = document.createElement('div');
  shortcuts.style.cssText = 'padding:10px 12px;border-top:1px solid var(--line);font-size:11px';
  shortcuts.innerHTML = `
    <div style="font-weight:700;color:var(--accent);margin-bottom:6px;text-transform:uppercase;letter-spacing:.1em">Quick Screens</div>
    <button class="btn" style="width:100%;margin-bottom:4px;font-size:11px;padding:6px" onclick="runScreener('undervalued')">🎯 Undervalued</button>
    <button class="btn" style="width:100%;font-size:11px;padding:6px" onclick="runScreener('growth')">📈 High Growth</button>
  `;
  document.querySelector('.sidebar').insertBefore(shortcuts, document.querySelector('.side-foot'));
};

// Auto-initialize
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    enhanceSidebar();
    if (DATA && DATA.user) loadWatchlists();
  });
} else {
  enhanceSidebar();
  if (typeof DATA !== 'undefined' && DATA.user) loadWatchlists();
}

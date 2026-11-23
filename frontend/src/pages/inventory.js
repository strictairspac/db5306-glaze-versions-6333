import {heading} from '../components/common/heading.js';
import {records} from '../components/common/records.js';
import {inventoryLabels} from '../constants/workflow.js';
import {topic} from '../constants/project.js';
import {date, esc} from '../utils/dom.js';
import {submit} from '../hooks/submit.js';

const HEADERS = ['盘点单', '水位', '确认进度', '状态', '开单人', '开单时间'];

export function inventoryPage(state, identity) {
  const orders = state.inventory;
  const selected = orders.find(row=>row.id===identity) || orders.find(row=>row.status==='active') || orders[0];
  const rows = orders.map(row=>[`<a href="#inventory/${row.id}" ${row.id===selected?.id?'aria-current="true"':''}>${esc(row.title)}</a>`,
    esc(row.watermark), `${row.confirmed_pages} / ${row.page_count}`, esc(inventoryLabels[row.status] || row.status),
    esc(row.owner), esc(date(row.created_at))]);
  const catalog = records(HEADERS, rows, '尚未开立盘点单。经办员可在配方版本档案页开单。');
  if (!selected) return `${heading(topic.modules[4], '称料组分批盘点：开单固定水位，按页确认。')}${catalog}`;
  const operator = state.session.role==='operator';
  const next = selected.next_page;
  const pages = selected.pages.map(page=>{
    const confirmed = Boolean(page.confirmed_at);
    const current = next===page.page_index;
    const items = page.items.map(item=>[esc(item.formula_id), esc(item.commit_seq), esc(item.operation), esc(item.recipe ?? '—')]);
    const action = confirmed ? `<p class="note">已确认 · ${esc(page.confirmed_by)} · ${esc(date(page.confirmed_at))}</p>`
      : current && operator ? `<form id="inventory-confirm-form" data-order="${selected.id}" data-page="${page.page_index}"><button>确认第 ${page.page_index+1} 页</button></form>`
      : '<p class="note">待确认</p>';
    return `<details class="panel" ${current?'open':''}><summary>第 ${page.page_index+1} 页 · ${page.items.length} 个配方 · ${confirmed?'已确认':current?'待确认 · 断点续盘':'待确认'}</summary>
      ${records(['配方编号', '提交序号', '操作', '釉料配方'], items)}${action}</details>`;
  }).join('');
  const brief = `<h2>${esc(selected.title)}</h2>
    <dl><dt>盘点水位</dt><dd>${esc(selected.watermark)}</dd><dt>每页配方数</dt><dd>${esc(selected.page_size)}</dd>
    <dt>确认进度</dt><dd>${selected.confirmed_pages} / ${selected.page_count}</dd><dt>状态</dt><dd>${esc(inventoryLabels[selected.status] || selected.status)}</dd>
    <dt>开单人</dt><dd>${esc(selected.owner)}</dd><dt>开单时间</dt><dd>${esc(date(selected.created_at))}</dd>
    ${selected.completed_at?`<dt>完成时间</dt><dd>${esc(date(selected.completed_at))}</dd>`:''}</dl>
    ${next===null||next===undefined ? '<p class="note">全部页已确认，本单不再为回收预检留存版次。</p>'
      : `<p class="note">断点续盘：当前待确认第 ${next+1} 页。未确认页命中的配方版次会在回收预检中留存。</p>`}`;
  return `${heading(topic.modules[4], '开单固定水位，翻页不重不漏；确认幂等，中断后回到未确认页。')}
    <div class="spread"><section aria-label="盘点单目录">${catalog}</section><aside class="brief">${brief}</aside></div>${pages}`;
}

export function bindInventory(api, refresh, fail) {
  const form = document.getElementById('inventory-confirm-form');
  if (!form) return;
  submit('inventory-confirm-form', async()=>{
    await api(`/inventory/${form.dataset.order}/pages/${form.dataset.page}/confirm`, {method:'POST'});
    await refresh();
  }, fail);
}

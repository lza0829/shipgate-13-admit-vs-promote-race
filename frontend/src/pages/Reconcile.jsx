import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function Reconcile() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const load = () => api('/对账').then(setData).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  if (err) return <div className="panel err">{err}</div>
  if (!data) return <div className="panel">加载中</div>
  const s = data.发版统计 || {}
  return (
    <div className="panel">
      <h2>发布对账</h2>
      <div className="grid">
        {Object.entries(s).map(([k, v]) => (
          <div className="stat" key={k}><div className="n">{v}</div><div className="l">{k}</div></div>
        ))}
        <div className="stat"><div className="n">{data.晋升事件数}</div><div className="l">晋升事件数</div></div>
        <div className={`stat ${data.全部通过 ? 'check-ok' : 'check-bad'}`}>
          <div className="n">{data.全部通过 ? '全绿' : '有裂'}</div>
          <div className="l">争用一致性</div>
        </div>
      </div>
      <h2>争用一致性检查</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>检查项</th><th>结论</th><th>明细</th></tr></thead>
          <tbody>
            {(data.一致性检查 || []).map((c, i) => (
              <tr key={i} className={c.通过 ? 'row-ok' : 'row-bad'}>
                <td>{c.项}</td>
                <td>{c.通过 ? '通过' : '不通过'}</td>
                <td className="muted">{typeof c.明细 === 'string' ? c.明细 : JSON.stringify(c.明细)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h2>在途登记</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>发版单号</th><th>阶段</th><th>持锁时间</th><th>过期时间</th><th>超时</th></tr></thead>
          <tbody>
            {(data.在途登记 || []).map((l) => (
              <tr key={l.编号} className={l.已超时 ? 'row-bad' : ''}>
                <td>{l.发版单号}</td><td>{l.阶段}</td><td>{l.持锁时间}</td><td>{l.过期时间}</td>
                <td>{l.已超时 ? '是' : '否'}</td>
              </tr>
            ))}
            {(data.在途登记 || []).length === 0 && <tr><td colSpan="5" className="muted">无在途登记</td></tr>}
          </tbody>
        </table>
      </div>
      <h2>争用记录</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>时间</th><th>关联</th><th>动作</th><th>争用戳</th></tr></thead>
          <tbody>
            {(data.争用记录 || []).map((c) => (
              <tr key={c.编号}><td>{c.时间}</td><td>{c.关联编号}</td><td>{c.动作}</td><td>{c.争用戳}</td></tr>
            ))}
            {(data.争用记录 || []).length === 0 && <tr><td colSpan="4" className="muted">无争用</td></tr>}
          </tbody>
        </table>
      </div>
      <h2>名册与实例</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>应用</th><th>环境</th><th>名册版本</th><th>实例版本们</th><th>红灯数</th></tr></thead>
          <tbody>
            {(data.名册与实例 || []).map((r, i) => (
              <tr key={i}><td>{r.应用}</td><td>{r.环境}</td><td>{r.名册版本}</td><td>{r.实例版本们 || '-'}</td><td>{r.红灯数}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row"><button type="button" className="secondary" onClick={load}>刷新</button></div>
    </div>
  )
}

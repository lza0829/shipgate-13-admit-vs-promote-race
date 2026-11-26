import React, { useEffect, useState } from 'react'
import { api } from '../api'

const KINDS = ['', '争用', '发版单', '晋升', '回滚', '放行窗', '回滚窗', '系统', '应用', '设置', '探针']

export default function Audit() {
  const [rows, setRows] = useState([])
  const [kind, setKind] = useState('')
  const [err, setErr] = useState('')
  const load = () =>
    api(kind ? `/审计?类别=${encodeURIComponent(kind)}` : '/审计')
      .then(setRows)
      .catch((e) => setErr(e.message))
  useEffect(() => { load() }, [kind])
  return (
    <div className="panel">
      <h2>审计流水</h2>
      {err && <p className="err">{err}</p>}
      <div className="row">
        <label>类别
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            {KINDS.map((k) => <option key={k} value={k}>{k || '全部'}</option>)}
          </select>
        </label>
        <button type="button" className="secondary" onClick={load}>刷新</button>
      </div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>时间</th><th>类别</th><th>动作</th><th>关联</th><th>备注/争用戳</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.编号} className={r.类别 === '争用' ? 'row-contention' : ''}>
                <td>{r.时间}</td><td>{r.类别}</td><td>{r.动作}</td>
                <td>{r.关联编号 || '-'}</td>
                <td className={r.备注 && r.备注.startsWith('CT-') ? 'stamp-cell' : ''}>{r.备注}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

import React, { useState, useEffect } from 'react';
import axios from 'axios';

/**
 * Страница депозитарной сверки (Whitepaper v2.1, раздел 10).
 * Режимы: off (только просмотр), shadow (информация для 1С), active (блокировка расхождений).
 * Доступно ролям: admin, operator, auditor, treasurer.
 */
const Reconciliation = () => {
  const [runs, setRuns] = useState([]);
  const [selectedRun, setSelectedRun] = useState(null);
  const [mode, setMode] = useState('shadow'); // off | shadow | active
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Загрузка списка запусков сверки
  useEffect(() => {
    fetchRuns();
  }, []);

  const fetchRuns = async () => {
    try {
      setLoading(true);
      const res = await axios.get('/api/custody/reconciliation/runs');
      setRuns(res.data);
      setLoading(false);
    } catch (err) {
      setError('Ошибка загрузки данных сверки');
      setLoading(false);
    }
  };

  // Запуск новой сверки
  const runReconciliation = async (sourceId, periodFrom, periodTo) => {
    try {
      setLoading(true);
      await axios.post('/api/custody/reconciliation/run', {
        source_id: sourceId,
        period_from: periodFrom,
        period_to: periodTo,
        mode: mode
      });
      await fetchRuns();
      setLoading(false);
    } catch (err) {
      setError('Ошибка запуска сверки');
      setLoading(false);
    }
  };

  // Экспорт отчета (XLSX/PDF)
  const exportReport = async (runId, format) => {
    window.open(`/api/custody/reconciliation/${runId}/export?format=${format}`, '_blank');
  };

  return (
    <div className="reconciliation-container">
      <h1>Депозитарная сверка: Блокчейн ↔ Выписка</h1>
      
      {error && <div className="alert alert-danger">{error}</div>}
      
      <div className="controls">
        <label>Режим: 
          <select value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="off">Off (Просмотр)</option>
            <option value="shadow">Shadow (Инфо для 1С)</option>
            <option value="active">Active (Блокировка)</option>
          </select>
        </label>
        <button onClick={() => runReconciliation('mock-depo', '2026-09-01', '2026-09-30')} disabled={loading}>
          Запустить сверку
        </button>
      </div>

      {loading && <div>Загрузка...</div>}

      <table className="table">
        <thead>
          <tr>
            <th>ID</th>
            <th>Источник</th>
            <th>Период</th>
            <th>Статус</th>
            <th>Расхождения</th>
            <th>Действия</th>
          </tr>
        </thead>
        <tbody>
          {runs.map(run => (
            <tr key={run.id}>
              <td>{run.id}</td>
              <td>{run.source_id}</td>
              <td>{run.period_from} — {run.period_to}</td>
              <td>{run.stale ? 'Устарел' : 'Актуален'}</td>
              <td>{run.discrepancies_count}</td>
              <td>
                <button onClick={() => setSelectedRun(run)}>Детали</button>
                <button onClick={() => exportReport(run.id, 'xlsx')}>XLSX</button>
                <button onClick={() => exportReport(run.id, 'pdf')}>PDF</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {selectedRun && (
        <div className="modal">
          <h3>Детали сверки #{selectedRun.id}</h3>
          <pre>{JSON.stringify(selectedRun, null, 2)}</pre>
          <button onClick={() => setSelectedRun(null)}>Закрыть</button>
        </div>
      )}
    </div>
  );
};

export default Reconciliation;

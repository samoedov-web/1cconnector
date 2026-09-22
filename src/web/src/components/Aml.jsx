import React, { useState, useEffect } from 'react';
import axios from 'axios';

/**
 * Страница AML-скрининга и решений комплаенса (Whitepaper v2.1, раздел 5).
 * Доступно ролям: admin, compliance, operator.
 */
const Aml = () => {
  const [screenings, setScreenings] = useState([]);
  const [reviews, setReviews] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    fetchData();
  }, []);

  const fetchData = async () => {
    setLoading(true);
    const [scrRes, revRes] = await Promise.all([
      axios.get('/api/aml/screenings'),
      axios.get('/api/aml/reviews')
    ]);
    setScreenings(scrRes.data);
    setReviews(revRes.data);
    setLoading(false);
  };

  const approveReview = async (reviewId) => {
    await axios.post(`/api/aml/reviews/${reviewId}/approve`);
    fetchData();
  };

  const rejectReview = async (reviewId, note) => {
    await axios.post(`/api/aml/reviews/${reviewId}/reject`, { note });
    fetchData();
  };

  return (
    <div className="aml-container">
      <h1>AML Скрининг и Комплаенс</h1>
      
      {loading && <div>Загрузка...</div>}

      <h2>Ожидающие решения (Review)</h2>
      <table className="table">
        <thead>
          <tr>
            <th>Адрес</th>
            <th>Риск</th>
            <th>Категории</th>
            <th>Действия</th>
          </tr>
        </thead>
        <tbody>
          {reviews.map(review => (
            <tr key={review.id}>
              <td>{review.address}</td>
              <td>{review.risk_score}</td>
              <td>{review.categories.join(', ')}</td>
              <td>
                <button onClick={() => approveReview(review.id)} className="btn-success">Одобрить</button>
                <button onClick={() => rejectReview(review.id, 'Manual reject')} className="btn-danger">Отклонить</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>История скринингов</h2>
      <table className="table">
        <thead>
          <tr>
            <th>Дата</th>
            <th>Адрес</th>
            <th>Риск</th>
            <th>Провайдер</th>
          </tr>
        </thead>
        <tbody>
          {screenings.map(scr => (
            <tr key={scr.id}>
              <td>{scr.screened_at}</td>
              <td>{scr.address}</td>
              <td>{scr.risk_score}</td>
              <td>{scr.source_id}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

export default Aml;

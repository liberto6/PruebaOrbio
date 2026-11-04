import React from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';

createRoot(document.getElementById('root')).render(
  <main>
    <p className="brand">Grupo Sazón</p>
    <h1>Estamos preparando nuestro proceso de selección</h1>
    <p>Esta es una versión inicial de desarrollo. El chat de selección y el panel
      del equipo todavía no están disponibles.</p>
    <p className="note">Aún no puedes enviar una candidatura desde esta página.</p>
  </main>,
);

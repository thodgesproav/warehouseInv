import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import legacy from '@vitejs/plugin-legacy'

export default defineConfig({
  plugins: [
    react(),
    legacy({
      // Serve native module chunks to current browsers while retaining a
      // classic SystemJS fallback for the TSW-1060 and other embedded clients.
      renderModernChunks: true,
      targets: ['Chrome >= 49', 'Android >= 5'],
    }),
    {
      // Crestron's WebView reports otherwise useful runtime errors as the
      // opaque "Script error." when Vite marks same-origin scripts CORS.
      name: 'tsw-same-origin-scripts',
      enforce: 'post',
      transformIndexHtml(html) {
        return html.replace(/\s+crossorigin(?=[\s>])/g, '')
      },
    },
  ],
  server: {proxy: {'/api': 'http://localhost:8000'}},
})

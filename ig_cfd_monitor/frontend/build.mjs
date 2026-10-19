import {build} from 'esbuild';
await build({
  entryPoints:['src/TradingViewReference.tsx'],
  outfile:'../web/vendor/tv-reference.js',
  bundle:true,
  format:'iife',
  platform:'browser',
  target:['es2020'],
  minify:true,
  legalComments:'eof',
  define:{'process.env.NODE_ENV':'"production"'},
  banner:{js:'/* Harbour isolated TradingView reference. React 18.3.1; react-ts-tradingview-widgets 1.2.8. See tv-reference.LICENSE and tv-reference.SOURCE.txt. */'},
});

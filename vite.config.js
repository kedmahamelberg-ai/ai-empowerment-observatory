import {defineConfig} from 'vite';
export default defineConfig({
  root:'_site',
  server:{host:'0.0.0.0',port:4173,strictPort:true,allowedHosts:['terminal.local']},
});

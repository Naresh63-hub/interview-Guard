const jsdom = require('jsdom');
const { JSDOM } = jsdom;
const dom = new JSDOM('<!DOCTYPE html><html><body><div id="btn-chat"></div></body></html>', { runScripts: 'dangerously', url: 'http://127.0.0.1:5000/' });
const fs = require('fs');
const script = fs.readFileSync('static/ui.js', 'utf8');
try {
    dom.window.eval(script);
    console.log('Success!');
} catch (e) {
    console.error('JS Error:', e);
}

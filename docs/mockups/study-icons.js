/* Line icons (24 grid, 1.6 stroke). Use <b class="ic" data-i="pen"></b>; call icons() after the DOM exists. */
const ICON = {
  today:'<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  notes:'<path d="M6 3.5h8.5L19 8v12.5H6z"/><path d="M14 3.5V8h5M9.5 13h6M9.5 16.5h6"/>',
  chat:'<path d="M4 5h16v11H9.5L5 20v-4H4z"/>',
  cards:'<rect x="3" y="8" width="14" height="11" rx="2"/><path d="M7 8V6a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2"/>',
  paper:'<path d="M7 3.5h10v17H7z"/><path d="M10 8h4M10 12h4M10 16h2"/>',
  plus:'<path d="M12 5v14M5 12h14"/>', camera:'<path d="M4 8h3l1.8-2.8h6.4L17 8h3v11H4z"/><circle cx="12" cy="13.2" r="3.4"/>',
  mic:'<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"/>',
  send:'<path d="M12 19V5M6 11l6-6 6 6"/>', pen:'<path d="M4 20l.9-4L16 4.9l3.1 3.1L8 19.1z"/><path d="M13.5 7.5l3 3"/>',
  eraser:'<path d="M4.5 15.5l9-9 6 6-5.5 5.5H8.5z"/><path d="M9 11l6 6M10.5 20.5H20"/>',
  lasso:'<ellipse cx="12" cy="9.5" rx="8" ry="5" stroke-dasharray="3 2.6"/><path d="M9.5 14c-1.8 2.8 0 5.8 2.8 5.8"/>',
  ruler:'<rect x="2.5" y="8.5" width="19" height="7" rx="1.2" transform="rotate(-30 12 12)"/><path d="M8 12.5l1.2.7M11 10.8l.8 1.3M14 9l1.2.7" transform="rotate(0)"/>',
  shape:'<rect x="3" y="11" width="9" height="9" rx="1"/><circle cx="16" cy="8" r="5"/>',
  image:'<rect x="3" y="4.5" width="18" height="15" rx="2"/><circle cx="9" cy="10" r="1.7"/><path d="M3 17.5l6-5 4 3 3-3 5 4.5"/>',
  link:'<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  text:'<path d="M5 6h14M12 6v13M9 19h6"/>', sticky:'<path d="M4 4.5h16v10l-5.5 5.5H4z"/><path d="M14.5 20v-5.5H20"/>',
  connect:'<circle cx="5.5" cy="6" r="2.2"/><circle cx="18.5" cy="18" r="2.2"/><path d="M7.7 6H12a4 4 0 0 1 4 4v5.8"/>',
  undo:'<path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>', redo:'<path d="M15 14l5-5-5-5"/><path d="M20 9H10a6 6 0 0 0 0 12h3"/>',
  check:'<path d="M5 12.5l4.5 4.5L19 7.5"/>', x:'<path d="M6 6l12 12M18 6L6 18"/>', cursor:'<path d="M5.5 3.5l13 7.5-6 1.8-2 6z"/>',
  flag:'<path d="M6 21V4M6 5h11l-2 4 2 4H6"/>', export:'<path d="M12 15V4M7.5 8.5L12 4l4.5 4.5M5 14v6h14v-6"/>',
  plan:'<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M9 3v4M15 3v4"/>',
  cyph:'<rect x="4" y="6" width="16" height="13" rx="3.5"/><path d="M9 12v1.5M15 12v1.5M4 11l-1.5-3M20 11l1.5-3"/>',
  more:'<circle cx="6" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="18" cy="12" r="1.2"/>',
  file:'<path d="M7 3.5h7l4 4v13H7z"/><path d="M14 3.5v4h4"/>', chev:'<path d="M9 6l6 6-6 6"/>', stack:'<path d="M12 4l9 4.5-9 4.5-9-4.5z"/><path d="M3 12.5l9 4.5 9-4.5M3 16.5l9 4.5 9-4.5"/>',
  pdf:'<path d="M7 3.5h7l4 4v13H7z"/><path d="M10 15.5h4M10 12.5h4"/>', book:'<path d="M5 4.5h11a3 3 0 0 1 3 3V20H8a3 3 0 0 1-3-3z"/><path d="M5 17a3 3 0 0 1 3-3h11"/>'
};
function icons(root = document) { root.querySelectorAll("[data-i]").forEach(el => { if (!el.firstChild) el.innerHTML = `<svg viewBox="0 0 24 24">${ICON[el.dataset.i] || ""}</svg>`; el.classList.add("ic"); }); }

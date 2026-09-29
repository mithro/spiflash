// Sorting for every spiflash table, and a filter box for the long ones
// (class sf-filterable). No dependencies.
(function () {
  "use strict";

  var UNITS = { B: 1, KiB: 1024, MiB: 1048576, GiB: 1073741824, V: 1 };

  // A cell's sort key: sizes ("16 MiB") and plain numbers sort numerically,
  // everything else as text.
  function key(cell) {
    var text = (cell.textContent || "").trim();
    var plain = text.replace(/,/g, "");
    var m = plain.match(/^([\d.]+)\s*(B|KiB|MiB|GiB|V)$/);
    if (m) return [0, parseFloat(m[1]) * UNITS[m[2]]];
    if (/^\d+$/.test(plain)) return [0, parseInt(plain, 10)];
    if (text === "—" || text === "") return [1, ""];
    return [0, text.toLowerCase()];
  }

  function compare(a, b) {
    if (a[0] !== b[0]) return a[0] - b[0];
    if (a[1] < b[1]) return -1;
    if (a[1] > b[1]) return 1;
    return 0;
  }

  function makeSortable(table) {
    var heads = table.querySelectorAll("thead th");
    var body = table.tBodies[0];
    if (!body) return;
    table.classList.add("sf-sortable");
    heads.forEach(function (th, col) {
      th.setAttribute("tabindex", "0");
      function sort() {
        var asc = th.getAttribute("aria-sort") !== "ascending";
        heads.forEach(function (h) { h.removeAttribute("aria-sort"); });
        th.setAttribute("aria-sort", asc ? "ascending" : "descending");
        var rows = Array.prototype.slice.call(body.rows);
        rows.sort(function (r1, r2) {
          var c = compare(key(r1.cells[col]), key(r2.cells[col]));
          return asc ? c : -c;
        });
        rows.forEach(function (r) { body.appendChild(r); });
      }
      th.addEventListener("click", sort);
      th.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); sort(); }
      });
    });
  }

  function makeFilterable(table) {
    var body = table.tBodies[0];
    if (!body) return;
    var box = document.createElement("div");
    box.className = "sf-filter";
    var input = document.createElement("input");
    input.type = "search";
    input.placeholder = "Filter: id, part, vendor, size…";
    input.setAttribute("aria-label", "Filter the table");
    var count = document.createElement("span");
    count.className = "sf-count";
    box.appendChild(input);
    // A table of counts (class sf-hide-zero) can hide its rows whose last
    // column is 0: hidden to start with.
    var toggle = null;
    if (table.classList.contains("sf-hide-zero")) {
      var label = document.createElement("label");
      label.className = "sf-toggle";
      toggle = document.createElement("input");
      toggle.type = "checkbox";
      toggle.checked = true;
      label.appendChild(toggle);
      label.appendChild(document.createTextNode(" Hide those with none"));
      box.appendChild(label);
    }
    box.appendChild(count);
    var anchor = table.closest(".table-wrapper") || table;
    anchor.parentNode.insertBefore(box, anchor);
    var rows = Array.prototype.slice.call(body.rows);
    var texts = rows.map(function (r) { return r.textContent.toLowerCase(); });
    var zero = rows.map(function (r) {
      return r.cells[r.cells.length - 1].textContent.trim() === "0";
    });
    function update() {
      var words = input.value.toLowerCase().split(/\s+/).filter(Boolean);
      var shown = 0;
      rows.forEach(function (r, i) {
        var ok = words.every(function (w) { return texts[i].indexOf(w) >= 0; });
        if (toggle && toggle.checked && zero[i]) ok = false;
        r.hidden = !ok;
        if (ok) shown++;
      });
      count.textContent = shown + " of " + rows.length;
    }
    input.addEventListener("input", update);
    if (toggle) toggle.addEventListener("change", update);
    update();
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("table.sf-table").forEach(makeSortable);
    document.querySelectorAll("table.sf-filterable").forEach(makeFilterable);
  });
})();

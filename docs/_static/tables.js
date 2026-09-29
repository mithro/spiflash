// Sorting for every spiflash table, a filter box for the long ones (class
// sf-filterable), and a nearest-part-name box (class sf-nearest). No
// dependencies.
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
        function order(r1, r2) {
          var c = compare(key(r1.cells[col]), key(r2.cells[col]));
          return asc ? c : -c;
        }
        // A sub-row (its first cell an sf-sub) stays under the row above
        // it, sorted among its siblings.
        var groups = [];
        Array.prototype.forEach.call(body.rows, function (r) {
          if (groups.length && r.cells[0].querySelector(".sf-sub")) {
            groups[groups.length - 1].subs.push(r);
          } else {
            groups.push({ row: r, subs: [] });
          }
        });
        groups.sort(function (g1, g2) { return order(g1.row, g2.row); });
        groups.forEach(function (g) {
          body.appendChild(g.row);
          g.subs.sort(order).forEach(function (r) { body.appendChild(r); });
        });
      }
      th.addEventListener("click", sort);
      th.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); sort(); }
      });
    });
  }

  // The column headed "Parts", if there is one.
  function partsColumn(table) {
    var heads = Array.prototype.map.call(table.querySelectorAll("thead th"), function (th) {
      return th.textContent.trim();
    });
    return heads.indexOf("Parts");
  }

  // A shell-style pattern (* any run, ? any one, [...] one of a set, [!...]
  // one not in it) as a regular expression for a whole name, ignoring case:
  // as spiflash.Database.find_glob reads it.
  function globRegExp(glob) {
    var rx = "";
    for (var i = 0; i < glob.length; i++) {
      var c = glob[i];
      if (c === "*") rx += ".*";
      else if (c === "?") rx += ".";
      else if (c === "[" && glob.indexOf("]", i + 2) > 0) {
        var end = glob.indexOf("]", i + 2);
        var set = glob.slice(i + 1, end).replace(/\\/g, "\\\\");
        if (set[0] === "!") set = "^" + set.slice(1);
        else if (set[0] === "^") set = "\\" + set;
        rx += "[" + set + "]";
        i = end;
      } else rx += c.replace(/[.*+?^${}()|[\]\\\/]/g, "\\$&");
    }
    return new RegExp("^(?:" + rx + ")$", "i");
  }

  // What the filter box holds: /.../ is a regular expression searched for
  // in the part names; otherwise each word must be in the row, and a word
  // with * ? or [ in it is a glob for a whole part name. Where a table has
  // no Parts column, the patterns are tried on every cell.
  function parseFilter(text) {
    var m = text.trim().match(/^\/(.+)\/$/);
    if (m) {
      try {
        return { tests: [{ rx: new RegExp(m[1], "i"), whole: false }] };
      } catch (e) {
        return { error: "not a regular expression" };
      }
    }
    var tests = text.toLowerCase().split(/\s+/).filter(Boolean).map(function (w) {
      return /[*?[]/.test(w) ? { rx: globRegExp(w), whole: true } : { word: w };
    });
    return { tests: tests };
  }

  function makeFilterable(table) {
    var body = table.tBodies[0];
    if (!body) return;
    var box = document.createElement("div");
    box.className = "sf-filter";
    var input = document.createElement("input");
    input.type = "search";
    input.placeholder = "Filter: id, part, vendor, size… or W25Q128*, /^MX25[LU]/";
    input.title =
      "Words match anywhere in a row; a word with * ? or [...] is a glob for a " +
      "whole part name (W25Q128*); /.../ is a regular expression on the part names.";
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
    // What a pattern is tried on: each part name, or each cell.
    var col = partsColumn(table);
    var items = rows.map(function (r) {
      if (col >= 0 && r.cells[col]) {
        return r.cells[col].textContent.split(",").map(function (n) { return n.trim(); });
      }
      return Array.prototype.map.call(r.cells, function (c) { return c.textContent.trim(); });
    });
    var zero = rows.map(function (r) {
      return r.cells[r.cells.length - 1].textContent.trim() === "0";
    });
    function update() {
      var filter = parseFilter(input.value);
      input.setAttribute("aria-invalid", filter.error ? "true" : "false");
      var tests = filter.tests || [];
      var shown = 0;
      rows.forEach(function (r, i) {
        var ok = tests.every(function (t) {
          if (t.word) return texts[i].indexOf(t.word) >= 0;
          return items[i].some(function (n) { return t.rx.test(n); });
        });
        if (toggle && toggle.checked && zero[i]) ok = false;
        r.hidden = !ok;
        if (ok) shown++;
      });
      count.textContent = (filter.error ? filter.error + "; " : "") + shown + " of " + rows.length;
    }
    input.addEventListener("input", update);
    if (toggle) toggle.addEventListener("change", update);
    update();
  }

  // Nearest part names, scored as spiflash.model.name_distance scores them:
  // an edit distance on letters and digits (a "." in a database name matches
  // any one), where an edit costs 4 and a character past the end of the
  // other name 1.
  var EDIT = 4;
  var TAIL = 1;

  function squash(name, wildcards) {
    return name.toUpperCase().replace(wildcards ? /[^0-9A-Z.]/g : /[^0-9A-Z]/g, "");
  }

  // [cost, leading characters in common] of squashed query a and name b.
  function distance(a, b) {
    function same(i, j) { return b[j] === "." || b[j] === a[i]; }
    var d = [[]];
    var i, j;
    for (j = 0; j <= b.length; j++) d[0].push(j * EDIT);
    for (i = 1; i <= a.length; i++) {
      var row = [i * EDIT];
      for (j = 1; j <= b.length; j++) {
        var cost = Math.min(
          d[i - 1][j - 1] + (same(i - 1, j - 1) ? 0 : EDIT),
          d[i - 1][j] + EDIT,
          row[j - 1] + EDIT
        );
        if (i > 1 && j > 1 && same(i - 1, j - 2) && same(i - 2, j - 1)) {
          cost = Math.min(cost, d[i - 2][j - 2] + EDIT);
        }
        row.push(cost);
      }
      d.push(row);
    }
    var best = Infinity;
    for (i = 0; i <= a.length; i++) best = Math.min(best, d[i][b.length] + (a.length - i) * TAIL);
    for (j = 0; j <= b.length; j++) best = Math.min(best, d[a.length][j] + (b.length - j) * TAIL);
    var common = 0;
    while (common < Math.min(a.length, b.length) && same(common, common)) common++;
    return [best, common];
  }

  // As spiflash.db._reason words it.
  function reason(a, b, common) {
    if (common === a.length && common === b.length) return "the same part";
    if (common === b.length) return "the query adds " + a.slice(common);
    if (common === a.length) return "the name adds " + b.slice(common);
    if (common) return "differs after " + a.slice(0, common);
    return "differs from the first character";
  }

  // [cost, -common, has a wildcard, name]: smaller is closer.
  function compareKeys(k1, k2) {
    for (var i = 0; i < k1.length; i++) {
      if (k1[i] < k2[i]) return -1;
      if (k1[i] > k2[i]) return 1;
    }
    return 0;
  }

  // A box above the table (class sf-nearest) listing the chips whose part
  // names are closest to what is typed in it, as `spiflash find --nearest`
  // does.
  function makeNearest(table) {
    var body = table.tBodies[0];
    var col = partsColumn(table);
    if (!body || col < 0) return;
    var heads = Array.prototype.map.call(table.querySelectorAll("thead th"), function (th) {
      return th.textContent.trim();
    });
    var vendorCol = heads.indexOf("Vendor");
    var chips = Array.prototype.map.call(body.rows, function (r) {
      var link = r.cells[0].querySelector("a");
      return {
        names: r.cells[col].textContent.split(",").map(function (n) { return n.trim(); }),
        id: link ? link.textContent.trim() : r.cells[0].textContent.trim(),
        href: link ? link.getAttribute("href") : null,
        vendor: vendorCol >= 0 ? r.cells[vendorCol].textContent.trim() : "",
      };
    });
    var box = document.createElement("div");
    box.className = "sf-filter sf-nearest-box";
    var input = document.createElement("input");
    input.type = "search";
    input.placeholder = "Nearest part name: a marking, an order code…";
    input.setAttribute("aria-label", "Find the nearest part names");
    box.appendChild(input);
    var list = document.createElement("ol");
    list.className = "sf-nearest-list";
    list.hidden = true;
    var anchor = table.closest(".table-wrapper") || table;
    anchor.parentNode.insertBefore(box, anchor);
    anchor.parentNode.insertBefore(list, anchor);
    function update() {
      var a = squash(input.value, false);
      list.textContent = "";
      list.hidden = !a;
      if (!a) return;
      var ranked = chips.map(function (c, i) {
        var best = null;
        c.names.forEach(function (n) {
          var b = squash(n, true);
          var dc = distance(a, b);
          var k = [dc[0], -dc[1], n.indexOf(".") >= 0 ? 1 : 0, n];
          if (!best || compareKeys(k, best.key) < 0) best = { key: k, name: n, b: b };
        });
        return { chip: c, best: best, order: i };
      });
      ranked.sort(function (x, y) {
        return compareKeys(x.best.key, y.best.key) || x.order - y.order;
      });
      ranked.slice(0, 10).forEach(function (r) {
        var li = document.createElement("li");
        var score = document.createElement("span");
        score.className = "sf-nearest-score";
        score.textContent = r.best.key[0];
        score.title = "score: 4 per edit, 1 per character past the end of the other name";
        li.appendChild(score);
        var name = document.createElement(r.chip.href ? "a" : "span");
        if (r.chip.href) name.href = r.chip.href;
        name.className = "sf-nearest-name";
        name.textContent = r.best.name;
        li.appendChild(name);
        var id = document.createElement("span");
        id.className = "sf-id";
        id.textContent = r.chip.id;
        li.appendChild(document.createTextNode(" "));
        li.appendChild(id);
        var why = r.chip.vendor ? r.chip.vendor + ": " : "";
        why += reason(a, r.best.b, -r.best.key[1]);
        li.appendChild(document.createTextNode(" " + why));
        list.appendChild(li);
      });
    }
    input.addEventListener("input", update);
    update();
  }

  // Under node, for the tests (tests/test_tables_js.py) to check the
  // scoring against the Python code's.
  if (typeof module !== "undefined") {
    module.exports = { squash: squash, distance: distance, reason: reason,
                       globRegExp: globRegExp, parseFilter: parseFilter };
    return;
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("table.sf-table").forEach(makeSortable);
    // The nearest box goes above the filter box.
    document.querySelectorAll("table.sf-nearest").forEach(makeNearest);
    document.querySelectorAll("table.sf-filterable").forEach(makeFilterable);
  });
})();

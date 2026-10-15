---
hide:
  - navigation
  - toc
---

# API reference

The reference below is rendered by [Redoc](https://github.com/Redocly/redoc) from
[`openapi.json`](openapi.json){#openapi-spec}, the API's contract, which the site copies from
`apps/api/openapi.json` each time it is built. The [overview](index.md) describes the design
behind these routes. A running server also serves FastAPI's interactive documentation at `/docs`.

<noscript>
  The reference needs JavaScript. The contract itself is the openapi.json file linked above.
</noscript>

<style>
  /* this page only: give the reference the width of the window */
  .md-main__inner.md-grid { max-width: 100rem; }
  #redoc-container { margin: 0 1.2rem 2rem; background: #fff; color: #333; color-scheme: light;
                     border-radius: 0.2rem; }
</style>

<div id="redoc-container"></div>

<script src="https://cdn.jsdelivr.net/npm/redoc@2.5.4/bundles/redoc.standalone.js"
        integrity="sha384-w447zOpYfw/1Tv/5AK9NfHTlQIqE3RVR6KY62jCyy9zNDgO64cMwGGP1Fj0zJVf5"
        crossorigin="anonymous"></script>
<script>
  (function () {
    var container = document.getElementById("redoc-container");
    // Outside the article, so the theme's typography does not restyle Redoc's own.
    var article = container.closest(".md-content__inner");
    if (article) { article.parentNode.appendChild(container); }
    if (!window.Redoc) {
      container.textContent = "The reference could not be loaded. The contract itself is the openapi.json file linked above.";
      return;
    }
    Redoc.init(document.getElementById("openapi-spec").href, {
      scrollYOffset: ".md-header",
      hideHostname: true,
      expandResponses: "200,202",
      theme: {
        typography: {
          fontFamily: "Roboto, -apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif",
          headings: { fontFamily: "Roboto, -apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif" },
          code: { fontFamily: "'Roboto Mono', SFMono-Regular, Consolas, Menlo, monospace" }
        }
      }
    }, container);
  })();
</script>

() => ({
              page_title: document.title || '',
              og_title: document.querySelector('meta[property="og:title"]')?.content || '',
              site_name: document.querySelector('meta[property="og:site_name"]')?.content || '',
              heading: document.querySelector('h1')?.innerText || '',
            })
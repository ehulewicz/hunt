// A tiny stand-in for the Cloud Run API's public surface in e2e: it serves the
// stored resume PDF with the backend's own headers.
import { createServer } from "node:http";

const PDF = "%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n";

createServer((request, response) => {
  if (request.url === "/health") {
    response.writeHead(200).end("ok");
    return;
  }
  if (request.url === "/v1/public/resume.pdf") {
    response.writeHead(200, {
      "Content-Type": "application/pdf",
      "Content-Disposition": 'inline; filename="Alex_Example.pdf"',
      "Cache-Control": "public, max-age=60",
    });
    response.end(PDF);
    return;
  }
  response.writeHead(404).end();
}).listen(4310);

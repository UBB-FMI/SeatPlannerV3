document.querySelector("#print-button").addEventListener("click", () => window.print());

document.querySelector("#print-labels").addEventListener("change", (event) => document.body.classList.toggle("overlay-labels", event.target.checked));

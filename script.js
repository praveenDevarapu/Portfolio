function toggleMenu() {
  const menuLinks = document.querySelector('.menu-links');
  const hamburgerIcon = document.querySelector('.hamburger-icon');
  menuLinks.classList.toggle('open');
  hamburgerIcon.classList.toggle('open');
}

// Close menu when clicking outside
document.addEventListener('click', function (e) {
  const hamburgerNav = document.getElementById('hamburger-nav');
  const menuLinks = document.querySelector('.menu-links');
  const hamburgerIcon = document.querySelector('.hamburger-icon');

  if (hamburgerNav && !hamburgerNav.contains(e.target)) {
    menuLinks.classList.remove('open');
    hamburgerIcon.classList.remove('open');
  }
});
from despereaux.repos.books import _sort_name


def test_sort_name_person():
    assert _sort_name("John Smith") == "Smith, John"


def test_sort_name_article():
    assert _sort_name("The Hobbit") == "Hobbit, The"
    assert _sort_name("A Wizard") == "Wizard, A"


def test_sort_name_single_word():
    assert _sort_name("Plato") == "Plato"

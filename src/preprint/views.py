__copyright__ = "Copyright 2017 Birkbeck, University of London"
__author__ = "Martin Paul Eve & Andy Byers"
__license__ = "AGPL v3"
__maintainer__ = "Birkbeck Centre for Technology and Publishing"

import operator
from functools import reduce

from django.shortcuts import render, redirect, get_object_or_404, HttpResponse
from django.utils import timezone
from django.db.models import Q
from django.urls import reverse
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.contrib.auth.decorators import login_required
from django.contrib import messages

from preprint import forms
from submission import models as submission_models, forms as submission_forms, logic
from core import models as core_models
from metrics.logic import store_article_access
from utils import shared as utils_shared


def preprints_home(request):
    """
    Displays the preprints home page with search box and 6 latest preprints publications
    :param request: HttpRequest object
    :return: HttpResponse
    """
    preprints = submission_models.Article.preprints.filter(
        date_published__lte=timezone.now()).order_by('-date_published')[:6]

    template = 'preprints/home.html'
    context = {
        'preprints': preprints,
    }

    return render(request, template, context)


def preprints_about(request):
    """
    Displays the about page with text about preprints
    :param request: HttpRequest object
    :return: HttpResponse
    """
    template = 'preprints/about.html'
    context = {

    }

    return render(request, template, context)


def preprints_list(request):
    """
    Displays a list of all published preprints.
    :param request: HttpRequest
    :return: HttpResponse
    """
    articles = submission_models.Article.preprints.filter(date_published__lte=timezone.now())

    paginator = Paginator(articles, 15)
    page = request.GET.get('page', 1)

    try:
        articles = paginator.page(page)
    except PageNotAnInteger:
        articles = paginator.page(1)
    except EmptyPage:
        articles = paginator.page(paginator.num_pages)

    template = 'preprints/list.html'
    context = {
        'articles': articles,
    }

    return render(request, template, context)


def preprints_search(request, search_term=None):
    """
    Searches through preprints based on their titles and authors
    :param request: HttpRequest
    :param search_term: Optional string
    :return: HttpResponse
    """
    if search_term:
        split_search_term = search_term.split(' ')

        article_search = submission_models.Article.preprints.filter(
            (Q(title__icontains=search_term) |
             Q(subtitle__icontains=search_term) |
             Q(keywords__word__in=split_search_term))
        )
        article_search = [article for article in article_search]

        institution_query = reduce(operator.and_, (Q(institution__icontains=x) for x in split_search_term))

        from_author = core_models.Account.objects.filter(
            (Q(first_name__in=split_search_term) |
             Q(last_name__in=split_search_term) |
             institution_query)
        )

        articles_from_author = [article for article in submission_models.Article.preprints.filter(
            authors__in=from_author)]
        articles = set(article_search + articles_from_author)

    else:
        articles = submission_models.Article.preprints.all()

    if request.POST:
        search_term = request.POST.get('search_term')
        return redirect(reverse('preprints_search_with_term', kwargs={'search_term': search_term}))


    template = 'preprints/search.html'
    context = {
        'search_term': search_term,
        'articles': articles,
    }

    return render(request, template, context)


def preprints_article(request, article_id):
    """
    Fetches a single article and displays its metadata
    :param request: HttpRequest
    :param article_id: integer, PK of an Article object
    :return: HttpResponse or Http404 if object not found
    """
    article = get_object_or_404(submission_models.Article.preprints.prefetch_related('authors'), pk=article_id,
                                stage=submission_models.STAGE_PUBLISHED,
                                date_published__lte=timezone.now())

    try:
        pdf = article.galley_set.get(type='pdf')
    except core_models.Galley.DoesNotExist:
        pdf = None

    store_article_access(request, article, 'view')

    template = 'preprints/article.html'
    context = {
        'article': article,
        'galleys': article.galley_set.all(),
        'pdf': pdf,
    }

    return render(request, template, context)


def preprints_pdf(request, article_id):

    pdf_url = request.GET.get('file')

    template = 'preprints/pdf.html'
    context = {
        'pdf_url': pdf_url,
    }
    return render(request, template, context)


def preprints_submit(request, article_id=None):
    """
    Handles initial steps of generating a preprints submission.
    :param request: HttpRequest
    :return: HttpResponse or HttpRedirect
    """
    if article_id:
        article = get_object_or_404(submission_models.Article.preprints, pk=article_id)
    else:
        article = None

    form = forms.PreprintInfo(instance=article)

    if request.POST:
        form = forms.PreprintInfo(request.POST, instance=article)

        if form.is_valid():
            article = form.save()
            article.owner = request.user
            article.is_preprint = True
            article.current_step = 1
            article.authors.add(request.user)
            article.correspondence_author = request.user
            article.save()
            return redirect(reverse('preprints_authors', kwargs={'article_id': article.pk}))

    template = 'preprints/submit_start.html'
    context = {
        'form': form
    }

    return render(request, template, context)


@login_required
def preprints_authors(request, article_id):
    article = get_object_or_404(submission_models.Article.preprints, pk=article_id, owner=request.user)

    form = submission_forms.AuthorForm()
    error, modal = None, None

    # If someone is attempting to add a new author
    if request.POST and 'add_author' in request.POST:
        form = submission_forms.AuthorForm(request.POST)
        modal = 'author'

        # Check if the author exists, if they do, add them without creating a new account
        author_exists = logic.check_author_exists(request.POST.get('email'))
        if author_exists:
            article.authors.add(author_exists)
            submission_models.ArticleAuthorOrder.objects.get_or_create(article=article,
                                                                       author=author_exists,
                                                                       defaults={'order': article.next_author_sort()})
            messages.add_message(request, messages.SUCCESS, '%s added to the article' % author_exists.full_name())
            return redirect(reverse('preprints_authors', kwargs={'article_id': article_id}))
        else:
            # Of the author isn't in the db, create a dummy account for them
            if form.is_valid():
                new_author = form.save(commit=False)
                new_author.username = new_author.email
                new_author.set_password(utils_shared.generate_password())
                new_author.save()
                article.authors.add(new_author)
                submission_models.ArticleAuthorOrder.objects.get_or_create(article=article,
                                                                           author=new_author,
                                                                           defaults={
                                                                               'order': article.next_author_sort()})
                messages.add_message(request, messages.SUCCESS, '%s added to the article' % new_author.full_name())

                return redirect(reverse('preprints_authors', kwargs={'article_id': article_id}))


    # If a user is trying to search for author without using the modal
    elif request.POST and 'search_authors' in request.POST:
        search = request.POST.get('author_search_text')

        try:
            search_author = core_models.Account.objects.get(Q(email=search) | Q(orcid=search))
            article.authors.add(search_author)
            submission_models.ArticleAuthorOrder.objects.get_or_create(article=article,
                                                                       author=search_author,
                                                                       defaults={'order': article.next_author_sort()})
            messages.add_message(request, messages.SUCCESS, '%s added to the article' % search_author.full_name())
        except core_models.Account.DoesNotExist:
            messages.add_message(request, messages.WARNING, 'No author found with those details.')


    # Handles posting from drag and drop.
    elif request.POST and 'authors[]' in request.POST:
        author_pks = [int(pk) for pk in request.POST.getlist('authors[]')]
        for author in article.authors.all():
            order = author_pks.index(author.pk)
            author_order, c = submission_models.ArticleAuthorOrder.objects.get_or_create(
                article=article,
                author=author,
                defaults={'order': order}
            )

            if not c:
                author_order.order = order
                author_order.save()

        return HttpResponse('Complete')

    # Handle deleting an author
    elif request.POST and 'delete_author' in request.POST:
        author_id = request.POST.get('delete_author')
        author_to_delete = get_object_or_404(core_models.Account, pk=author_id)
        # Delete the author-article ordering
        submission_models.ArticleAuthorOrder.objects.filter(article=article, author=author_to_delete).delete()
        # Remove the author from the article
        article.authors.remove(author_to_delete)
        # Add message and redirect
        messages.add_message(request, messages.SUCCESS, 'Author removed from article.')
        return redirect(reverse('preprints_authors', kwargs={'article_id': article_id}))

    template = 'preprints/authors.html'
    context = {
        'article': article,
        'form': form,
        'modal': modal,
    }

    return render(request, template, context)

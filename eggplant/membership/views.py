# -*- coding: utf-8 -*-
import logging

from django.http import Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.core.urlresolvers import reverse, reverse_lazy
from django.contrib import messages
from django.contrib.auth import login, authenticate, logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required, permission_required
from django.views.generic import FormView
from django.conf import settings
from django.db import transaction

from allauth.account.models import EmailAddress
from allauth.account.views import sensitive_post_parameters_m, \
    PasswordSetView, PasswordChangeView

from ..common.views import LoginRequiredMixinView
from .forms import (ProfileForm, DepartmentInvitationForm,
                    AcceptInvitationForm, NewUserSetPasswordForm)
from .models import (
    DepartmentInvitation,
    UserProfile,
    Account,
)
from .utils import create_verified_user


log = logging.getLogger(__name__)


@permission_required('membership.can_invite')
@login_required
def invite(request):
    """Invite a new email address."""
    form = DepartmentInvitationForm()
    if request.method == 'POST':
        form = DepartmentInvitationForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']
            existing_user = User.objects.filter(email=email)
            existing_email = EmailAddress.objects.get_users_for(email=email)
            if existing_user or existing_email:
                msg = 'User {} already exists.'.format(email)
                messages.error(request, msg)
            else:
                DepartmentInvitation.objects.create(
                    email=email,
                    invited_by=request.user,
                    department=form.cleaned_data['department'],
                    account_category=form.cleaned_data['account_category']
                )
                msg = 'Invitation has been send to {}'.format(email)
                messages.add_message(request, messages.SUCCESS, msg)
                return redirect(reverse('eggplant:dashboard:home'))
    ctx = {
        'form': form,
        'title': "send invitation",
    }
    return render(request, 'eggplant/membership/invite.html', ctx)


class AlreadyAcceptedInvitationException(Exception):
    pass


def do_accept_invitation(request, invitation):
    email = invitation.email
    existing_user = User.objects.filter(email=email).exists()
    existing_email = EmailAddress.objects.get_users_for(email=email)
    if existing_user or existing_email:
        msg = "You have already accepted invitation for this email."
        messages.error(request, msg)
        log.debug("already accepted")
        raise AlreadyAcceptedInvitationException()
    invitation.accepted = True
    invitation.save()

    with transaction.atomic():
        user = create_verified_user(invitation)
        user.profile.save()
        account = Account.objects.create(
            category=invitation.account_category,
            department=invitation.department
        )
        account.user_profiles.add(user.profile)

    # authenticate user via InvitationBackend
    user = authenticate(username=invitation.email,
                        password=invitation.verification_key.hex)
    return user


def accept_invitation(request, verification_key):
    """Accept invitation."""
    if request.user.is_authenticated():
        msg = "You are already logged-in"
        messages.add_message(request, messages.WARNING, msg)
        return redirect(reverse('eggplant:dashboard:home'))
    invitation = get_object_or_404(DepartmentInvitation,
                                   verification_key=verification_key,
                                   accepted=False)
    form = AcceptInvitationForm()
    if request.method == 'POST':
        if settings.USE_RECAPTCHA:
            form = AcceptInvitationForm(request.POST)
            if form.is_valid():
                try:
                    user = do_accept_invitation(request, invitation)
                except AlreadyAcceptedInvitationException:
                    return redirect(reverse('eggplant:dashboard:home'))
            else:
                user = None
                msg = 'Invalid captcha.'
                messages.add_message(request, messages.WARNING, msg)
        else:
            # If not using recaptcha we don't show
            # the form so there is no POST
            pass
    else:
        if not settings.USE_RECAPTCHA:
            try:
                user = do_accept_invitation(request, invitation)
            except AlreadyAcceptedInvitationException:
                return redirect(reverse('eggplant:dashboard:home'))
        # if this is GET and we use recaptcha just render the form

    if user:
        login(request, user)
        request.session['new-invited-user'] = True
        messages.success(request, 'You have accepted the invitation.')
        return redirect(reverse('account_set_password'))
    ctx = {
        'form': form,
        'verification_key': verification_key,
        'title': "accept invitation",
    }
    return render(request, 'eggplant/membership/accept_invitation.html', ctx)


class NewUserPasswordView(LoginRequiredMixinView, PasswordSetView):
    """
    Set password only for a new user. Existing users can use password change.
    """
    success_url = reverse_lazy('eggplant:membership:profile')
    form_class = NewUserSetPasswordForm

    def get_authenticated_redirect_url(self, *args, **kwargs):
        return self.success_url

    def get_success_url(self, *args, **kwargs):
        return self.success_url

    def form_valid(self, form):
        form.save()  # FIXME: password_set signal which redirects to login

    def get(self, request, *args, **kwargs):
        if not request.session.get('new-invited-user'):
            raise Http404()
        return super(NewUserPasswordView, self).get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        profile = self.request.user.profile
        if profile.is_complete() or \
                not request.session.pop('new-invited-user', False):
            # existing user
            msg = "User with completed profile %s is trying to set password."
            log.warn(msg, self.request.user)
            raise Http404()
        form = self.get_form()
        if form.is_valid():
            self.form_valid(form)
            return redirect(self.success_url)
        else:
            return self.form_invalid(form)

    @sensitive_post_parameters_m
    def dispatch(self, request, *args, **kwargs):
        """Overrides PasswordSetView.dispatch with the View.dispatch"""
        if request.method.lower() in self.http_method_names:
            handler = getattr(
                self,
                request.method.lower(),
                self.http_method_not_allowed
            )
        else:
            handler = self.http_method_not_allowed
        return handler(request, *args, **kwargs)
sets_new_user_password = NewUserPasswordView.as_view()


class LoginAfterPasswordChangeView(LoginRequiredMixinView, PasswordChangeView):
    """
    After password change, log user out and redirect to account_login.
    """
    success_url = reverse_lazy('account_login')

    def form_valid(self, form):
        ret = super(LoginAfterPasswordChangeView, self).form_valid(form)
        logout(self.request)
        return ret

loginpage_password_change = LoginAfterPasswordChangeView.as_view()


class ProfileView(LoginRequiredMixinView, FormView):
    """Profile form view."""
    form_class = ProfileForm
    template_name = 'eggplant/membership/profile.html'
    success_url = reverse_lazy('eggplant:dashboard:home')

    def get_object(self, queryset=None):
        self.objects = UserProfile.objects.get(user_id=self.request.user.id)
        return self.objects

    def get_initial(self):
        initial = {
            'first_name': self.request.user.first_name,
            'last_name': self.request.user.last_name,
        }
        initial.update(self.get_object().__dict__)
        return initial

    def form_valid(self, form):
        user_id = self.request.user.id
        User.objects.filter(id=user_id)\
            .update(first_name=form.cleaned_data['first_name'],
                    last_name=form.cleaned_data['last_name'])
        del form.cleaned_data['first_name']
        del form.cleaned_data['last_name']
        UserProfile.objects.filter(user_id=user_id).update(**form.cleaned_data)
        msg = "Your profile has been successfully updated."
        messages.success(self.request, msg)
        return super(ProfileView, self).form_valid(form)

    def get_context_data(self, **kwargs):
        context = super(ProfileView, self).get_context_data(**kwargs)
        context['form'] = kwargs['form']
        return context
profile = ProfileView.as_view()

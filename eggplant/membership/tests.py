# -*- coding: utf-8 -*-
import os
import datetime

from django.core import mail
from django.conf import settings
from django.test import TestCase
from django.core.urlresolvers import reverse
from django.contrib.auth.models import User
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType

from allauth.account.models import EmailAddress

from ..common.utils import absolute_url_reverse
from .models import (
    UserProfile,
    DepartmentInvitation,
    Account,
)
from .factories import (
    UserFactory,
    AccountFactory,
    AccountCategoryFactory,
    DepartmentFactory,
    DepartmentInvitationFactory,
)


class TestProfile(TestCase):

    def setUp(self):
        self.user = UserFactory()
        self.user.set_password('pass')
        self.user.save()
        email_address = EmailAddress.objects\
            .add_email(None, self.user, 'test@food.net', confirm=False,
                       signup=False)
        email_address.verified = True
        email_address.primary = True
        email_address.save()

    def test_profile(self):
        response = self.client.get(reverse('eggplant:membership:profile'))
        url = reverse('account_login') + '?next=' + \
            reverse('eggplant:membership:profile')
        self.assertRedirects(response, url, status_code=302,
                             target_status_code=200, msg_prefix='')

        self.client.login(username='test@food.net', password='pass')
        response = self.client.get(reverse('eggplant:membership:profile'))
        expected = '<form action="%s"' % reverse('eggplant:membership:profile')
        self.assertContains(response, expected, 1, 200)

        data = {
            'first_name': 'Joe',
            'middle_name': 'Frank',
            'last_name': 'Doe',
            'address': '123 Sunset av. NY, USA',
            'postcode': '123321ABCD',
            'city': 'New York',
            'tel': '79231232',
            'sex': '0',
            'date_of_birth': '11/12/13',
            'privacy': 'checked',
        }
        response = self.client.post(reverse('eggplant:membership:profile'),
                                    data=data)

        expected = {
            'middle_name': 'Frank',
            'address': '123 Sunset av. NY, USA',
            'postcode': '123321ABCD',
            'sex': 0,
            'city': 'New York',
            'tel': '79231232',
            'date_of_birth': datetime.date(2013, 11, 12),
            'privacy': True,
        }
        profile = UserProfile.objects.get(user_id=self.user.id)
        self.assertDictContainsSubset(expected, profile.__dict__)
        user = User.objects.get(id=self.user.id)
        self.assertEqual(user.first_name, 'Joe')
        self.assertEqual(user.last_name, 'Doe')

    def test_user_profile(self):
        self.assertIsNotNone(self.user.profile)

    def test_user_profile_privacy(self):
        self.assertFalse(self.user.profile.privacy)

    def test_user_member_department_models(self):
        # Although this may look like testing ORM API I thought it
        # would be good to just write a test to show how we expect
        # membership to work
        department = DepartmentFactory()
        user2 = UserFactory()
        user2.profile.save()
        account = AccountFactory(department=department)
        account.user_profiles.add(user2.profile)
        account.user_profiles.add(self.user.profile)
        self.assertEqual(2, account.user_profiles.all().count())

        # we don't have to have a fresh copy of dept
        self.assertEqual(1, department.accounts.count())
        department.accounts.all().delete()
        self.assertEqual(0, department.accounts.count())


class TestInvite(TestCase):

    def setUp(self):
        self.user = UserFactory(email='test_admin@food.net')
        self.user.set_password('pass')
        self.user.is_superuser = True

        self.department = DepartmentFactory()
        self.account_category = AccountCategoryFactory()

        content_type = ContentType.objects.get_for_model(DepartmentInvitation)
        can_invite = Permission.objects.get(content_type=content_type,
                                            codename='can_invite')
        self.user.user_permissions.add(can_invite)
        self.user.save()
        os.environ['RECAPTCHA_TESTING'] = 'True'

    def test_get(self):
        self.client.login(username=self.user.username, password='pass')
        response = self.client.get(reverse('eggplant:membership:invite'))
        self.assertTemplateUsed(response, 'eggplant/membership/invite.html')

    def test_user_already_verified(self):
        data = {
            'department': self.department.id,
            'email': self.user.email,
            'account_category': self.account_category.id,
        }
        self.client.login(username=self.user.email, password='pass')
        response = self.client.post(reverse('eggplant:membership:invite'),
                                    data=data, follow=True)
        expected = 'User {} already exists'.format(self.user.email)
        self.assertContains(response, expected, 1, 200)

    def test_send_invitation(self):
        invited_email = 'test1@localhost'
        data = {
            'department': self.department.id,
            'email': invited_email,
            'account_category': self.account_category.id,
        }
        self.client.login(username=self.user.username, password='pass')
        response = self.client.post(reverse('eggplant:membership:invite'),
                                    data=data, follow=True)

        self.assertRedirects(response, reverse('eggplant:dashboard:home'))

        expected = 'Invitation has been send to {}'.format(invited_email)
        self.assertContains(response, expected, 1, 200)

        self.assertEqual(len(mail.outbox), 1)  # @UndefinedVariable
        self.assertTrue(bool(mail.outbox[0].subject))

        invitation = DepartmentInvitation.objects.get(email=invited_email)

        url = absolute_url_reverse(
            'eggplant:membership:accept_invitation',
            kwargs=dict(verification_key=invitation.verification_key.hex)
        )
        self.assertRegex(invitation.verification_key.hex, r'^[a-z0-9]{32}\Z')
        self.assertIn(url, mail.outbox[0].body)

    def test_accept_invitation_flow(self):
        invited_email = 'test2@food.net'
        invitation = DepartmentInvitationFactory(
            email=invited_email,
            invited_by=self.user,
            department=self.department,
            account_category=self.account_category,
        )
        accept_invitation_url = reverse(
            'eggplant:membership:accept_invitation',
            kwargs=dict(verification_key=invitation.verification_key.hex)
        )

        if settings.USE_RECAPTCHA:
            response = self.client.get(accept_invitation_url)
            self.assertContains(response, 'accept invitation', 2)
            self.assertContains(response, invitation.verification_key.hex, 1)

            data = {
                'recaptcha_responseonse_field': 'PASSED',
            }
            response = self.client.post(
                accept_invitation_url,
                data=data,
                follow=True
            )
            url_name = 'account_set_password'
            self.assertRedirects(response, reverse(url_name),
                                 status_code=302,
                                 target_status_code=200,
                                 msg_prefix='',)
        else:
            response = self.client.get(accept_invitation_url, follow=True)
            self.assertRedirects(
                response,
                reverse('account_set_password'),
                status_code=302,
                target_status_code=200
            )
        self.assertContains(response, 'password1', 3)
        self.assertContains(response, 'password2', 3)

        # test for creating default account for new user
        actual = Account.objects.all().count()
        self.assertEqual(1, actual)
        actual = Account.objects.all()[0]
        test_user = User.objects.get(email=invited_email)
        self.assertEqual(actual.user_profiles.all()[0], test_user.profile)

        data = {
            'password1': 'passpass123',
            'password2': 'passpass123',
        }
        response = self.client.post(
            reverse('account_set_password'),
            data=data,
            follow=True
        )
        self.assertRedirects(
            response,
            reverse('account_login') + '?next=' + reverse('eggplant:membership:profile'),
            status_code=302,
            target_status_code=200
        )

        data = {
            'login': invited_email,
            'password': 'passpass123',
        }
        response = self.client.post(
            reverse('account_login'),
            data=data,
            follow=True
        )
        self.assertRedirects(
            response,
            reverse('eggplant:membership:profile'),
            status_code=302,
            target_status_code=200
        )

        # check if a new user is forced to complete it's profile
        response = self.client.get(reverse('eggplant:dashboard:home'),
                                   follow=True)
        self.assertRedirects(
            response,
            reverse('eggplant:membership:profile'),
            status_code=302,
            target_status_code=200
        )
        expected = 'Please update your profile.'
        actual = [m.message for m in list(response.context['messages'])]
        self.assertIn(expected, actual)

        data = {
            'first_name': 'first_name',
            'middle_name': '',
            'last_name': 'last_name',
            'address': 'Vestergade 20C',
            'city': 'Copenhagen',
            'postcode': '123321',
            'tel': '231321321',
            'sex': UserProfile.FEMALE,
            'date_of_birth': '1980-01-01',
            'privacy': '1',
        }
        response = self.client.post(reverse('eggplant:membership:profile'),
                                    data=data, follow=True)
        self.assertRedirects(
            response,
            reverse('eggplant:dashboard:home'),
            status_code=302,
            target_status_code=200
        )
        expected = 'Your profile has been successfully updated.'
        actual = [m.message for m in list(response.context['messages'])]
        self.assertIn(expected, actual)
        self.assertContains(response, 'Log out', 1)

    def test_change_password_get(self):
        self.client.login(username=self.user.username, password='pass')
        response = self.client.get(reverse('account_change_password'))
        self.assertTemplateUsed(response, 'account/password_change.html')
        self.assertContains(response, 'Change Password')
